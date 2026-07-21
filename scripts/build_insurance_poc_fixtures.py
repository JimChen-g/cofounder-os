"""Build and verify synthetic PDF/PNG fixtures for the insurance POC demo."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"
PDF_NAME = "insurance-poc-requirements.pdf"
IMAGE_NAMES = ("accident-intersection.png", "accident-damage.png")


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _draw_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    draw.rounded_rectangle((54, 48, 1346, 170), radius=24, fill="#151816")
    draw.text((92, 74), title, fill="#FFFFFF", font=_font(34, bold=True))
    draw.text((94, 122), subtitle, fill="#AFC9A1", font=_font(20))


def _build_intersection_image(destination: Path) -> None:
    image = Image.new("RGB", (1400, 900), "#F3F3EF")
    draw = ImageDraw.Draw(image)
    _draw_header(
        draw, "SYNTHETIC ACCIDENT EVIDENCE 01", "Signal-controlled intersection / not a real claim"
    )
    draw.rectangle((0, 350, 1400, 650), fill="#4E5550")
    draw.rectangle((550, 170, 850, 900), fill="#4E5550")
    for x in range(50, 1400, 150):
        draw.rectangle((x, 493, x + 72, 507), fill="#F6D76B")
    for y in range(205, 900, 120):
        draw.rectangle((693, y, 707, y + 54), fill="#F6D76B")
    draw.polygon([(655, 705), (745, 705), (700, 615)], fill="#76B900")
    draw.text((620, 742), "Vehicle A\nstraight", fill="#FFFFFF", font=_font(24, bold=True))
    draw.polygon([(1000, 442), (1000, 558), (890, 500)], fill="#FFB547")
    draw.text((1040, 455), "Vehicle B\nleft turn", fill="#FFFFFF", font=_font(24, bold=True))
    draw.ellipse((820, 420, 940, 580), outline="#FF675E", width=18)
    draw.text((830, 590), "impact zone", fill="#8B211C", font=_font(22, bold=True))
    draw.rounded_rectangle(
        (70, 710, 510, 842), radius=18, fill="#FFFFFF", outline="#D2D6D0", width=3
    )
    draw.text((100, 738), "Visible fixture facts", fill="#171A18", font=_font(24, bold=True))
    draw.text(
        (100, 780),
        "Cross-path geometry\nNo protected-turn evidence",
        fill="#4D554F",
        font=_font(21),
    )
    image.save(destination, format="PNG", optimize=True)


def _build_damage_image(destination: Path) -> None:
    image = Image.new("RGB", (1400, 900), "#F3F3EF")
    draw = ImageDraw.Draw(image)
    _draw_header(
        draw, "SYNTHETIC ACCIDENT EVIDENCE 02", "Damage-position diagram / not a real vehicle photo"
    )
    draw.rounded_rectangle(
        (90, 260, 650, 700), radius=64, fill="#DDE9F7", outline="#1A67C9", width=8
    )
    draw.rounded_rectangle(
        (750, 260, 1310, 700), radius=64, fill="#FFF0D7", outline="#A96100", width=8
    )
    draw.text((128, 300), "VEHICLE A", fill="#174E94", font=_font(34, bold=True))
    draw.text((788, 300), "VEHICLE B", fill="#7B4700", font=_font(34, bold=True))
    draw.polygon([(552, 475), (650, 415), (650, 570)], fill="#FF675E")
    draw.text((160, 500), "Front-right\nimpact", fill="#171A18", font=_font(31, bold=True))
    draw.rectangle((750, 430, 810, 585), fill="#FF675E")
    draw.text((885, 500), "Right-side\nimpact", fill="#171A18", font=_font(31, bold=True))
    draw.rounded_rectangle(
        (180, 746, 1220, 835), radius=18, fill="#FFFFFF", outline="#D2D6D0", width=3
    )
    draw.text(
        (218, 774),
        "Geometry supports scenario reconstruction but cannot establish liability alone.",
        fill="#4D554F",
        font=_font(23),
    )
    image.save(destination, format="PNG", optimize=True)


def _footer(canvas: object, document: object) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#5D635F"))
    canvas.drawString(20 * mm, 12 * mm, "Synthetic fixture - CoFounder OS insurance POC")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {document.page}")
    canvas.restoreState()


def _build_pdf(destination: Path) -> None:
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=23,
        leading=28,
        textColor=colors.HexColor("#171A18"),
        alignment=TA_LEFT,
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "HeadingCustom",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#416A00"),
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "BodyCustom",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#2F3531"),
        spaceAfter=5,
    )
    callout = ParagraphStyle(
        "Callout",
        parent=body,
        backColor=colors.HexColor("#ECF7D8"),
        borderColor=colors.HexColor("#76B900"),
        borderWidth=1,
        borderPadding=9,
        leading=14,
        spaceAfter=12,
    )
    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="Synthetic Insurer POC Requirements",
        author="CoFounder OS synthetic fixture builder",
    )
    story = [
        Paragraph("Insurance Claims Intelligence POC", title),
        Paragraph("Synthetic requirements brief - version 1.0 / ten business days", callout),
        Paragraph("1. Business objective", heading),
        Paragraph(
            "Demonstrate that a claims adjuster can submit a requirement document and two accident images, inspect a source-linked evidence board, and receive a governed, non-authoritative recommendation package within one working session.",
            body,
        ),
        Paragraph("2. Required users and workflow", heading),
        Paragraph(
            "Primary users are claims adjusters and claims team leads. The POC must preserve each source, confidence value, privacy classification, model route, Agent consumer, revision, and human decision in an audit-ready Run.",
            body,
        ),
        Paragraph("3. Mandatory capabilities", heading),
    ]
    requirements = [
        ["ID", "Requirement", "Acceptance"],
        [
            "R1",
            "Evidence ingestion",
            "One PDF and two synthetic PNG images are accepted or fail with a recoverable status.",
        ],
        [
            "R2",
            "Evidence traceability",
            "Every decision fact links to a source file and Evidence ID.",
        ],
        [
            "R3",
            "Model routing",
            "Local/private, multimodal/planning, Engineering, and Verifier routes are explained.",
        ],
        [
            "R4",
            "Human governance",
            "No claim-facing or external write occurs before an explicit human decision.",
        ],
        [
            "R5",
            "Decision package",
            "Six versioned deliverables pass checksum and schema validation.",
        ],
    ]
    table = Table(requirements, colWidths=[14 * mm, 42 * mm, 113 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#151816")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEADING", (0, 0), (-1, -1), 11),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9CDC7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F8F5")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 8 * mm),
            Paragraph("4. Privacy and authority boundary", heading),
            Paragraph(
                "Accident evidence is restricted by default. Cloud processing is allowed only for sanitized, minimum-necessary context. The POC must never present a model recommendation as an enforcement, legal, underwriting, or automatic claims decision.",
                body,
            ),
            PageBreak(),
            Paragraph("POC acceptance and delivery", title),
            Paragraph("5. Acceptance targets", heading),
        ]
    )
    targets = [
        ["Metric", "Target"],
        ["Stable fixture cases", "8"],
        ["Evidence traceability", "100% for cited decision facts"],
        ["Required Artifact integrity", "100%"],
        ["Unsupported claim facts", "0 accepted"],
        ["External writes without approval", "0"],
    ]
    target_table = Table(targets, colWidths=[75 * mm, 94 * mm], repeatRows=1)
    target_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#416A00")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9CDC7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F8F5")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            target_table,
            Paragraph("6. Required delivery package", heading),
            Paragraph(
                "Executive Decision Memo; Insurance POC Product Brief; Technical Implementation Plan; Budget Summary; Risk Register; and Two-week Action Plan. Code diff and test evidence are included only if the existing Engineering execution chain performs a real repository action.",
                body,
            ),
            Paragraph("7. Budget and scope", heading),
            Paragraph(
                "Maximum budget is CNY 50,000 including a CNY 5,000 reserve. Optional automated insurer write-back is out of scope when it would exceed this ceiling or violate the approval boundary.",
                body,
            ),
            Paragraph("8. Demonstration disclosure", heading),
            Paragraph(
                "The two image findings in the stable recording fixture may be generated by a checksum-bound synthetic Adapter. The product must label this provenance and must fail explicitly for unknown images until a formal multimodal Adapter is configured.",
                callout,
            ),
        ]
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> dict[str, object]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    _build_pdf(FIXTURE_DIR / PDF_NAME)
    _build_intersection_image(FIXTURE_DIR / IMAGE_NAMES[0])
    _build_damage_image(FIXTURE_DIR / IMAGE_NAMES[1])
    assets = []
    for name in (PDF_NAME, *IMAGE_NAMES):
        path = FIXTURE_DIR / name
        assets.append(
            {
                "filename": name,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "synthetic": True,
            }
        )
    manifest = {
        "schema_version": "insurance-poc-assets-1.0",
        "generated_by": "scripts/build_insurance_poc_fixtures.py",
        "assets": assets,
    }
    (FIXTURE_DIR / "asset-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(manifest: dict[str, object]) -> None:
    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 3:
        raise ValueError("asset manifest must contain exactly three binaries")
    for asset in assets:
        if not isinstance(asset, dict):
            raise TypeError("asset manifest entries must be objects")
        path = FIXTURE_DIR / str(asset["filename"])
        if not path.is_file():
            raise FileNotFoundError(path)
        if _sha256(path) != asset["sha256"]:
            raise ValueError(f"checksum mismatch: {path.name}")
        if path.stat().st_size != asset["size_bytes"]:
            raise ValueError(f"size mismatch: {path.name}")
    for name in IMAGE_NAMES:
        with Image.open(FIXTURE_DIR / name) as image:
            if image.format != "PNG" or image.size != (1400, 900):
                raise ValueError(f"unexpected image format or size: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        manifest = json.loads((FIXTURE_DIR / "asset-manifest.json").read_text(encoding="utf-8"))
    else:
        manifest = build()
    verify(manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
