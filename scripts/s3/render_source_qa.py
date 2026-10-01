"""Render selected financial source pages for visual verification."""
from pathlib import Path
import pdfplumber
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "storage/s3/corpora/s3-semiconductor-equipment-ar-v1/source_qa"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    groups = [("688072_2024", "688072_2024_annual_report.pdf", [152, 156, 159, 160]),
              ("688072_2023", "688072_2023_annual_report.pdf", [153, 157, 160, 161]),
              ("688082_2024", "688082_2024_annual_report.pdf", [126, 130, 134])]
    for name, filename, numbers in groups:
        with pdfplumber.open(ROOT / "storage/s3/raw" / filename) as pdf:
            images = [pdf.pages[p - 1].to_image(resolution=110).original.convert("RGB") for p in numbers]
            canvas = Image.new("RGB", (sum(im.width for im in images), max(im.height for im in images)), "white")
            x = 0
            for im in images:
                canvas.paste(im, (x, 0))
                x += im.width
            canvas.save(OUT / f"{name}.png")
            print(OUT / f"{name}.png", flush=True)


if __name__ == "__main__":
    main()
