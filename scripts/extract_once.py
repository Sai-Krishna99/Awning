"""Manual live check of menu extraction on one image (not a test)."""

import asyncio
import sys
from pathlib import Path

from anthropic import AsyncAnthropic

from awning.config import load_settings
from awning.extraction import MenuExtractor, to_menu_items
from awning.images import prepare_image


async def main(path: str) -> None:
    settings = load_settings()
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url
    )
    extractor = MenuExtractor(client, settings.extraction_model, settings.extraction_effort)
    image = prepare_image(Path(path).read_bytes())
    result = await extractor.extract(image)
    for item in to_menu_items(result.menu):
        price = "?" if item.price_cents is None else f"{item.price_cents / 100:.2f}"
        flag = " [review: " + item.review_note + "]" if item.needs_review else ""
        options = ", ".join(f"{o.name} {o.price_delta_cents:+d}" for o in item.options)
        print(f"{item.category or '-'} | {item.name} | {price}{flag}" + (f" | {options}" if options else ""))
    print(f"shop={result.menu.shop_name_guess} currency={result.menu.currency_guess}")
    print(f"suspicious_text={result.menu.suspicious_text}")
    print(
        f"items={len(result.menu.items)} model={result.model} in={result.input_tokens} "
        f"out={result.output_tokens} latency_ms={result.latency_ms}"
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
