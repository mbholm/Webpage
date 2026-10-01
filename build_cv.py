"""
Compile the CV and publish it to the website.

Run:  python build_cv.py

Compiles cv/Holm_CV.tex with pdflatex (twice, so page headers settle) and copies the
result to files/Holm_CV.pdf, which is the file the "Curriculum vitae" link points to.
Build by-products stay in cv/build/, which is ignored by Git.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CV_DIR = ROOT / "cv"
BUILD = CV_DIR / "build"
TARGET = ROOT / "files" / "Holm_CV.pdf"


def main():
    BUILD.mkdir(exist_ok=True)
    cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
           "-output-directory=build", "Holm_CV.tex"]
    for run in (1, 2):
        result = subprocess.run(cmd, cwd=CV_DIR, capture_output=True, text=True, errors="replace")
        if result.returncode != 0:
            print(result.stdout[-3000:])
            sys.exit(f"pdflatex failed on run {run}; full log in cv/build/Holm_CV.log")
    shutil.copyfile(BUILD / "Holm_CV.pdf", TARGET)
    print(f"CV compiled and copied to {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
