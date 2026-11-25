"""Send a chat message to the LicenseLens API."""

import argparse
import asyncio

import httpx


async def ask(message: str, base_url: str) -> None:
    """Send a message and display the API response."""
    url = f"{base_url.rstrip('/')}/v1/chat"

    payload = {
        "message": message,
        "temperature": 0.2,
        "purpose": "terminal_test",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)

        if response.is_error:
            print(f"API error: HTTP {response.status_code}")
            print(response.text)
            return

        data = response.json()

        print("\nLicenseLens:")
        print(data["answer"])

        print("\n--- Usage ---")
        print(f"Provider:       {data['provider']}")
        print(f"Model:          {data['model']}")
        print(f"Input tokens:   {data['tokens_in']}")
        print(f"Output tokens:  {data['tokens_out']}")
        print(f"Latency:        {data['latency_ms']} ms")
        print(f"Estimated cost: ₹{data['cost_inr']}")
        print(f"Fallback used:  {data['fallback_used']}")

    except httpx.ConnectError:
        print("Could not connect. Is the LicenseLens API running?")
    except httpx.TimeoutException as exc:
        print(f"Request timed out: {type(exc).__name__}: {exc}")
    except httpx.HTTPError as exc:
        print(f"HTTP error: {type(exc).__name__}: {exc}")
    except (KeyError, ValueError) as exc:
        print(f"Response error: {type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask LicenseLens a question.")
    parser.add_argument("message", help="Your question for LicenseLens")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="LicenseLens API base URL",
    )
    args = parser.parse_args()

    asyncio.run(ask(args.message, args.url))


if __name__ == "__main__":
    main()
