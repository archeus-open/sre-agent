"""Worker entrypoint sketch for checkout-api (sample)."""

from checkout import process_order

if __name__ == "__main__":
    # In production this consumes from a queue; here it just idles.
    print("checkout-api worker starting (sample)")
