"""Command line interface for PDF to Markdown converter."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from glob import glob
from pathlib import Path

from .converter import PdfToMarkdownConverter
from .plugins import KNOWN_BACKENDS
from .plugin_cli import PluginCli

logger = logging.getLogger(__name__)

# Backend names accepted by --backend (third-party entry-point names also work
# at runtime, but are not enumerated here).
_BACKEND_CHOICES = ["auto", "pdfplumber", *sorted(KNOWN_BACKENDS)]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Dispatch the `plugin` subcommand before the convert parser runs, so the
    # positional `input_files` (nargs="+") doesn't swallow the word "plugin".
    if argv and argv[0] == "plugin":
        raise SystemExit(PluginCli().run(argv[1:]))

    parser = argparse.ArgumentParser(
        description="Convert PDF files to Markdown format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  %(prog)s input.pdf                      # Output to stdout
  %(prog)s input.pdf -o output.md         # Output to file
  %(prog)s input.pdf --ignore-images      # Skip images, single file output
  %(prog)s *.pdf -o output_dir/           # Batch conversion
  %(prog)s input.pdf -p 1-3              # Convert only pages 1-3
  %(prog)s input.pdf --backend docling    # Use the docling (OCR) backend
  %(prog)s plugin list                    # List optional backends
  %(prog)s plugin install docling         # Install the docling backend
        """,
    )

    parser.add_argument(
        "input_files",
        nargs="+",
        help="Input PDF file paths (supports wildcards)",
    )
    parser.add_argument("-o", "--output", help="Output file or directory path")
    parser.add_argument(
        "--ignore-images",
        "--no-images",
        action="store_true",
        dest="ignore_images",
        help="Ignore all images and output a single Markdown file",
    )
    parser.add_argument(
        "-p",
        "--pages",
        help="Page range to convert (e.g., '1-5,8,10-12')",
    )
    parser.add_argument(
        "--backend",
        default="auto",
        help=(
            "Conversion backend: 'auto' (default), 'pdfplumber', 'docling' "
            "(after `plugin install docling`), or a third-party entry-point name."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose logs")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    converter = PdfToMarkdownConverter()

    try:
        for input_pattern in args.input_files:
            matching_files = glob(input_pattern)
            if not matching_files:
                logger.warning("No matching files found: %s", input_pattern)
                continue

            for file_path in matching_files:
                if not file_path.lower().endswith(".pdf"):
                    logger.warning("Skipping non-PDF file: %s", file_path)
                    continue

                output_path = None
                if args.output:
                    if os.path.isdir(args.output) or args.output.endswith("/"):
                        output_path = os.path.join(
                            args.output, f"{Path(file_path).stem}.md"
                        )
                    else:
                        output_path = args.output

                markdown_content = converter.convert_file(
                    file_path,
                    output_path=output_path,
                    ignore_images=args.ignore_images,
                    pages=args.pages,
                    backend=args.backend,
                )

                if not output_path:
                    print(f"\n=== {file_path} ===\n")
                    print(markdown_content)
    except KeyboardInterrupt:
        logger.info("Conversion interrupted by user")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Program execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
