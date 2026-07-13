#!/usr/bin/env python3
"""One-time downloader for the EOT20 global tide model (ADR 0004).

Fetches the EOT20 ocean-tide constituent files (CC-BY 4.0, ~2 GB, no
registration) from SEANOE and unpacks them into the tide-model directory that
`fetch_conditions.py` reads for its keyless, offline EOT20 tide rung. Run once:

    uv run --extra tides python download_tide_model.py

After that, tides work offline for any coastal spot with no API key. Set
EOT20_DIR to place the model somewhere other than the default cache location;
`fetch_conditions.py` reads the same variable.

This is an explicit setup utility, so unlike `fetch_conditions.py` it exits
non-zero on failure (the caller asked for a download and needs to know it broke).
"""

import os
import sys
import zipfile

import click
import httpx

DEFAULT_DIR = os.path.expanduser("~/.cache/claude-surfing-skills/tide_models")
# SEANOE dataset 00683/79489 (DOI 10.17882/79489), the EOT20 zip.
EOT20_ZIP_URL = "https://www.seanoe.org/data/00683/79489/data/85762.zip"


def _ocean_dir(target_dir: str) -> str:
    return os.path.join(target_dir, "EOT20", "ocean_tides")


@click.command()
@click.option(
    "--dir",
    "target_dir",
    default=lambda: os.environ.get("EOT20_DIR") or DEFAULT_DIR,
    help="Directory to install EOT20 into (must end up containing EOT20/ocean_tides/).",
)
@click.option("--url", default=EOT20_ZIP_URL, help="Override the EOT20 zip URL.")
@click.option("--keep-zip", is_flag=True, help="Keep the downloaded zip after extracting.")
def main(target_dir: str, url: str, keep_zip: bool) -> None:
    ocean = _ocean_dir(target_dir)
    if os.path.isdir(ocean) and os.listdir(ocean):
        click.echo(f"EOT20 already present at {ocean} - nothing to do.")
        return

    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(target_dir, "eot20.zip")

    click.echo(f"Downloading EOT20 (~2 GB, one time) to {zip_path}")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            tty = sys.stdout.isatty()  # avoid \r progress spam in captured logs
            next_mark = 0
            with open(zip_path, "wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
                    done += len(chunk)
                    if not total:
                        continue
                    if tty:
                        click.echo(f"\r  {done / 1e6:6.0f} / {total / 1e6:.0f} MB", nl=False)
                    elif done >= next_mark:  # log every ~10% when non-interactive
                        click.echo(f"  {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                        next_mark += total // 10
            click.echo("" if tty else "  download complete")
    except Exception as e:
        click.echo(f"Download failed: {e}", err=True)
        sys.exit(1)

    click.echo("Extracting ...")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(target_dir)
        # The SEANOE archive nests the atlases as inner zips (ocean_tides.zip,
        # load_tides.zip) that unpack to bare ocean_tides/ and load_tides/
        # folders. pyTMD's EOT20 model expects them under EOT20/, so extract
        # each inner zip into <dir>/EOT20/ and drop the inner zip.
        eot20_dir = os.path.join(target_dir, "EOT20")
        for inner in ("ocean_tides.zip", "load_tides.zip"):
            inner_path = os.path.join(target_dir, inner)
            if os.path.exists(inner_path):
                os.makedirs(eot20_dir, exist_ok=True)
                with zipfile.ZipFile(inner_path) as inner_zip:
                    inner_zip.extractall(eot20_dir)
                os.remove(inner_path)
    except Exception as e:
        click.echo(f"Extraction failed: {e}", err=True)
        sys.exit(1)
    finally:
        if not keep_zip and os.path.exists(zip_path):
            os.remove(zip_path)

    if os.path.isdir(ocean) and os.listdir(ocean):
        click.echo(f"Done. EOT20 ready at {target_dir}.")
        if target_dir != (os.environ.get("EOT20_DIR") or DEFAULT_DIR):
            click.echo(f"Point fetch_conditions.py at it with: export EOT20_DIR={target_dir}")
        return

    # The zip may nest the model under a top-level folder; locate it and tell
    # the user which EOT20_DIR to set rather than guessing a move.
    for root, _dirs, _files in os.walk(target_dir):
        if os.path.basename(root) == "ocean_tides" and os.path.basename(os.path.dirname(root)) == "EOT20":
            found = os.path.dirname(os.path.dirname(root))
            click.echo(f"EOT20 extracted under {found}")
            click.echo(f"Set: export EOT20_DIR={found}")
            return

    click.echo(
        "Extracted, but no EOT20/ocean_tides directory was found. Inspect the "
        f"archive layout under {target_dir}.",
        err=True,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
