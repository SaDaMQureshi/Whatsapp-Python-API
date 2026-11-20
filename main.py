
import os
import re
import time
import asyncio
import logging

from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("whatsapp-api")


# --------------------------------------------------
# Settings
# --------------------------------------------------

CHROME_DEBUG_URL = os.environ.get(
    "WA_CHROME_DEBUG_URL",
    "http://127.0.0.1:9222"
)

AFTER_SEND_WAIT = int(
    os.environ.get("WA_AFTER_SEND_WAIT", 5)
)

BULK_DELAY = int(
    os.environ.get("WA_BULK_DELAY", 3)
)

CHAT_LOAD_TIMEOUT = int(
    os.environ.get("WA_CHAT_LOAD_TIMEOUT", 60000)
)

MESSAGE_LOAD_TIMEOUT = int(
    os.environ.get("WA_MESSAGE_LOAD_TIMEOUT", 20000)
)

SEND_CONFIRM_TIMEOUT = int(
    os.environ.get("WA_SEND_CONFIRM_TIMEOUT", 20000)
)


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(
    title="WhatsApp Sender API",
    version="2.1.0"
)

send_lock = asyncio.Lock()


# --------------------------------------------------
# Models
# --------------------------------------------------

class SendRequest(BaseModel):
    phone: str = Field(
        ...,
        examples=["+923001234567"]
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=4000
    )


class SendResponse(BaseModel):
    phone: str
    message: str
    status: str


class BulkSendRequest(BaseModel):
    phones: List[str] = Field(
        ...,
        examples=[["+923001234567", "+923001234568"]]
    )

    message: str = Field(
        ...,
        min_length=1,
        max_length=4000
    )


class BulkSendResponse(BaseModel):
    results: List[SendResponse]
    total: int
    successful: int
    failed: int


# --------------------------------------------------
# Phone Number Normalization
# --------------------------------------------------

def normalize_phone(phone: str) -> str:

    if not isinstance(phone, str) or not phone.strip():
        raise HTTPException(
            status_code=422,
            detail="Phone number is required."
        )

    original = phone.strip()

    # Keep digits only.
    digits = re.sub(r"\D", "", original)

    # Convert international 00 prefix.
    if digits.startswith("00"):
        digits = digits[2:]

    # Pakistan local format: 03001234567 -> 923001234567
    if digits.startswith("0") and len(digits) == 11:
        digits = "92" + digits[1:]

    # Pakistan mobile format: 3001234567 -> 923001234567
    elif digits.startswith("3") and len(digits) == 10:
        digits = "92" + digits

    if not re.fullmatch(r"\d{10,15}", digits):
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid phone number. "
                "Use international format, "
                "e.g. +923001234567"
            )
        )

    return "+" + digits


# --------------------------------------------------
# Text Normalization
# --------------------------------------------------

def normalize_message_text(message: str) -> str:

    if message is None:
        return ""

    # Remove invisible Unicode formatting characters
    message = re.sub(
        r"[\u200B-\u200F\uFEFF]",
        "",
        message
    )

    # Normalize spaces and line breaks for comparison
    message = re.sub(r"\s+", " ", message)

    return message.strip()


# --------------------------------------------------
# WhatsApp Composer Helpers
# --------------------------------------------------

def get_composer(page):

    return page.locator(
        "footer [contenteditable='true']"
    ).first


def wait_for_message_in_composer(page, message: str):

    page.wait_for_function(
        """(expected) => {

            const composer = document.querySelector(
                "footer [contenteditable='true']"
            );

            if (!composer) {
                return false;
            }

            const normalize = (value) => {

                return (value || "")
                    .replace(/[\\u200B-\\u200F\\uFEFF]/g, "")
                    .replace(/\\s+/g, " ")
                    .trim();
            };

            const actual = normalize(
                composer.innerText || composer.textContent
            );

            const target = normalize(expected);

            return actual.length > 0 && actual === target;
        }""",
        arg=message,
        timeout=MESSAGE_LOAD_TIMEOUT
    )


def find_send_button(page):

    selectors = [
        "[data-testid='compose-btn-send']",
        "button[aria-label='Send']",
        "[aria-label='Send']",
        "button:has([data-icon='send'])"
    ]

    for selector in selectors:

        try:

            buttons = page.locator(selector)

            for index in range(buttons.count()):

                candidate = buttons.nth(index)

                if candidate.is_visible() and candidate.is_enabled():
                    return candidate

        except Exception:
            continue

    return None


# --------------------------------------------------
# WhatsApp Sending Function
# --------------------------------------------------

def _send_blocking(phone: str, message: str):

    number = phone.lstrip("+")

    # Open chat without URL-prefilling the message.
    url = (
        "https://web.whatsapp.com/send"
        f"?phone={number}"
    )

    logger.info(
        "Opening WhatsApp chat for %s",
        phone
    )

    logger.info(
        "Message length: %s characters",
        len(message)
    )

    page = None

    try:

        with sync_playwright() as p:

            # Connect to the existing Chrome session.
            browser = p.chromium.connect_over_cdp(
                CHROME_DEBUG_URL
            )

            if not browser.contexts:
                raise Exception(
                    "No Chrome browser context found."
                )

            context = browser.contexts[0]

            # Reuse the existing WhatsApp tab.
            page = next(
                (
                    tab for tab in context.pages
                    if "web.whatsapp.com" in tab.url
                ),
                None
            )

            if page is None:
                page = context.new_page()

            page.bring_to_front()

            # Navigate to recipient chat.
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=CHAT_LOAD_TIMEOUT
            )

            # Wait for WhatsApp composer.
            composer = get_composer(page)

            composer.wait_for(
                state="visible",
                timeout=CHAT_LOAD_TIMEOUT
            )

            logger.info(
                "WhatsApp chat opened for %s",
                phone
            )

            # Enter the message directly.
            composer.fill(
                message,
                timeout=MESSAGE_LOAD_TIMEOUT
            )

            # Verify message using normalized text.
            wait_for_message_in_composer(
                page,
                message
            )

            logger.info(
                "Message loaded in composer for %s",
                phone
            )

            # Find the actual Send button.
            send_button = find_send_button(page)

            if send_button is None:

                raise Exception(
                    "WhatsApp Send button not found. "
                    "Message was not submitted."
                )

            # Click Send.
            send_button.click(
                timeout=10000
            )

            logger.info(
                "Send button clicked for %s",
                phone
            )

            # Confirm that the composer is empty.
            page.wait_for_function(
                """() => {

                    const composer = document.querySelector(
                        "footer [contenteditable='true']"
                    );

                    if (!composer) {
                        return false;
                    }

                    const text = (
                        composer.innerText ||
                        composer.textContent ||
                        ""
                    )
                    .replace(/[\\u200B-\\u200F\\uFEFF]/g, "")
                    .trim();

                    return text === "";
                }""",
                timeout=SEND_CONFIRM_TIMEOUT
            )

            logger.info(
                "Message submitted successfully for %s",
                phone
            )

            time.sleep(AFTER_SEND_WAIT)

            # IMPORTANT:
            # Do not call browser.close().
            # Chrome is an existing remote-debugging session.
            # Keep it running for subsequent recipients.

        return True

    except PlaywrightTimeoutError as e:

        logger.error(
            "WhatsApp timeout for %s: %s",
            phone,
            str(e)
        )

        raise Exception(
            f"WhatsApp operation timed out for {phone}. "
            "Check the WhatsApp chat and message composer."
        ) from e

    except Exception:

        logger.exception(
            "Failed sending to %s",
            phone
        )

        raise


# --------------------------------------------------
# Single Message API
# --------------------------------------------------

@app.post(
    "/send",
    response_model=SendResponse
)
async def send_message(body: SendRequest):

    phone = normalize_phone(body.phone)

    async with send_lock:

        try:

            await asyncio.to_thread(
                _send_blocking,
                phone,
                body.message
            )

        except Exception as e:

            logger.error(
                "Single send failed for %s: %s",
                phone,
                str(e)
            )

            raise HTTPException(
                status_code=500,
                detail=f"Send failed: {str(e)}"
            )

    return SendResponse(
        phone=phone,
        message=body.message,
        status="sent"
    )


# --------------------------------------------------
# Bulk Message API
# --------------------------------------------------

@app.post(
    "/send-bulk",
    response_model=BulkSendResponse
)
async def send_bulk_message(body: BulkSendRequest):

    results = []

    successful = 0
    failed = 0

    async with send_lock:

        for index, phone in enumerate(body.phones):

            logger.info(
                "Bulk sending: %s/%s",
                index + 1,
                len(body.phones)
            )

            try:

                normalized = normalize_phone(phone)

                await asyncio.to_thread(
                    _send_blocking,
                    normalized,
                    body.message
                )

                results.append(
                    SendResponse(
                        phone=normalized,
                        message=body.message,
                        status="sent"
                    )
                )

                successful += 1

            except HTTPException as e:

                results.append(
                    SendResponse(
                        phone=phone,
                        message=body.message,
                        status=f"failed: {e.detail}"
                    )
                )

                failed += 1

                logger.error(
                    "Invalid recipient %s: %s",
                    phone,
                    e.detail
                )

            except Exception as e:

                results.append(
                    SendResponse(
                        phone=phone,
                        message=body.message,
                        status=f"failed: {str(e)}"
                    )
                )

                failed += 1

                logger.error(
                    "Bulk sending failed for %s: %s",
                    phone,
                    str(e)
                )

            # Delay between recipients.
            if index < len(body.phones) - 1:

                logger.info(
                    "Waiting %s seconds before next recipient...",
                    BULK_DELAY
                )

                await asyncio.sleep(BULK_DELAY)

    logger.info(
        "Bulk sending completed | Total: %s | Successful: %s | Failed: %s",
        len(body.phones),
        successful,
        failed
    )

    return BulkSendResponse(
        results=results,
        total=len(body.phones),
        successful=successful,
        failed=failed
    )


# --------------------------------------------------
# Health Check
# --------------------------------------------------

@app.get("/")
async def home():

    return {
        "status": "running",
        "service": "WhatsApp Sender API",
        "version": "2.1.0"
    }
