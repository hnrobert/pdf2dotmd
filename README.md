# pdf2dotmd

A Python CLI tool that converts PDF files to Markdown format with intelligent layout analysis.

## Features

- **Layout-aware text extraction** — reconstructs logical reading order from PDF spatial data
- **Multi-column detection** — handles two-column and multi-column layouts
- **Table extraction** — converts PDF tables to Markdown pipe tables
- **Heading inference** — detects headings from font size hierarchy
- **Header/footer filtering** — automatically removes repeated page headers and footers
- **Image extraction** — extracts embedded images to an `assets/` directory
- **Ignore images mode** — `--ignore-images` flag for text-only output
- **Page range selection** — convert specific pages only
- **Batch conversion** — process multiple PDF files with wildcards

## Installation

```bash
pip install pdf2dotmd
```

## Usage

```bash
# Output to stdout
pdf2dotmd input.pdf

# Output to file
pdf2dotmd input.pdf -o output.md

# Skip images, output single Markdown file
pdf2dotmd input.pdf --ignore-images

# Batch conversion
pdf2dotmd *.pdf -o output_dir/

# Convert only specific pages
pdf2dotmd input.pdf -p 1-3
pdf2dotmd input.pdf -p 1-5,8,10-12

# Verbose logging
pdf2dotmd input.pdf -v
```

## Backends & Plugins

The default install is lightweight (pdfplumber only) and converts **born-digital**
PDFs. For **scanned PDFs (OCR)**, **complex layouts**, and **borderless/complex
tables**, install the optional [docling](https://github.com/docling-project/docling)
backend (TableFormer + DocLayNet + OCR). It requires **Python >=3.10**.

```bash
# List available and installed backends
pdf2dotmd plugin list

# Install the docling backend (runs pip install pdf2dotmd[docling])
pdf2dotmd plugin install docling

# Show details / uninstall
pdf2dotmd plugin info docling
pdf2dotmd plugin uninstall docling
```

Once installed, choose a backend explicitly or let `auto` pick:

```bash
# Use docling directly
pdf2dotmd scanned.pdf --backend docling -o out.md

# auto (default): born-digital PDFs use pdfplumber; scanned PDFs use docling
pdf2dotmd document.pdf -o out.md
```

Third-party packages can register their own backend via the
`pdf2dotmd.backends` entry-point group — they appear in `plugin list`
automatically.

## How It Works

1. **Character extraction** — uses [pdfplumber](https://github.com/jsvine/pdfplumber) to extract individual characters with position data
2. **Line grouping** — clusters characters into text lines by y-coordinate proximity
3. **Block formation** — groups lines into paragraphs based on horizontal alignment and vertical spacing
4. **Column detection** — identifies multi-column layouts by analyzing horizontal text density gaps
5. **Reading order** — sorts blocks top-to-bottom, left-to-right, handling spanning titles
6. **Header/footer removal** — detects repeated elements across pages
7. **Heading inference** — maps font sizes to heading levels (H1-H6)

## Limitations

- **Scanned PDFs (default backend)** — the default pdfplumber backend has no OCR;
  scanned/image-only PDFs produce empty output. Install the docling backend
  (`pdf2dotmd plugin install docling`, Python >=3.10) to add OCR.
- **Encrypted PDFs** — password-protected PDFs are not supported
- **Complex layouts** — the default backend may not parse highly irregular
  layouts perfectly; the docling backend handles these better

## License

MIT
