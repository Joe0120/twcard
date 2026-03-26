"""CLI entry point for credit card statement processing."""

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Credit card statement downloader and parser"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download
    subparsers.add_parser("download", help="Download PDFs from Gmail")

    # parse
    subparsers.add_parser("parse", help="Parse downloaded PDFs to CSV")

    # run (full pipeline)
    run_parser = subparsers.add_parser("run", help="Full pipeline: download + parse")
    run_parser.add_argument(
        "--skip-download", action="store_true", help="Skip download step"
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "download":
        from .gmail_downloader import download_pdfs
        download_pdfs()

    elif args.command == "parse":
        from .pipeline import parse_all
        parse_all()

    elif args.command == "run":
        from .pipeline import run
        run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
