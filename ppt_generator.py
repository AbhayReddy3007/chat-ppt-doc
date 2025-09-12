from pptx import Presentation
from pptx.util import Pt, Inches
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE, MSO_VERTICAL_ANCHOR
import re


def clean_title_text(title: str) -> str:
    """Clean up titles for slides."""
    if not title:
        return "Presentation"
    title = re.sub(r"\s+", " ", title.strip())  # collapse multiple spaces/newlines
    return title


def create_ppt(title, points, filename="output.pptx"):
    prs = Presentation()

    # --- Brand Colors ---
    PRIMARY_PURPLE = RGBColor(94, 42, 132)   # #5E2A84
    SECONDARY_TEAL = RGBColor(0, 185, 163)   # #00B9A3
    TEXT_DARK = RGBColor(40, 40, 40)         # dark gray
    BG_LIGHT = RGBColor(244, 244, 244)       # light gray

    # Clean the title
    title = clean_title_text(title)

    # --- Title Slide ---
    slide_layout = prs.slide_layouts[5]  # blank layout
    slide = prs.slides.add_slide(slide_layout)

    # Background
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = PRIMARY_PURPLE

    # Title TextBox
    left, top, width, height = Inches(1), Inches(2), Inches(8), Inches(3)
    textbox = slide.shapes.add_textbox(left, top, width, height)
    tf = textbox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE  # vertically center

    p = tf.add_paragraph()
    p.text = title
    p.font.size = Pt(40)
    p.font.bold = True
    p.font.color.rgb = RGBColor(255, 255, 255)
    p.alignment = PP_ALIGN.CENTER  # horizontal center

    # --- Content Slides ---
    for idx, item in enumerate(points, start=1):
        key_point = clean_title_text(item.get("title", ""))
        description = item.get("description", "")

        slide = prs.slides.add_slide(prs.slide_layouts[5])

        # Alternate background for contrast
        bg_color = BG_LIGHT if idx % 2 == 0 else RGBColor(255, 255, 255)
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = bg_color

        # Slide Title
        left, top, width, height = Inches(0.8), Inches(0.5), Inches(8), Inches(1.5)
        textbox = slide.shapes.add_textbox(left, top, width, height)
        tf = textbox.text_frame
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        tf.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE

        p = tf.add_paragraph()
        p.text = key_point
        p.font.size = Pt(30)
        p.font.bold = True
        p.font.color.rgb = PRIMARY_PURPLE
        p.alignment = PP_ALIGN.LEFT

        # Accent underline
        shape = slide.shapes.add_shape(
            1, Inches(0.8), Inches(1.6), Inches(3), Inches(0.1)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = SECONDARY_TEAL
        shape.line.fill.background()

        # Description (bullets)
        if description:
            left, top, width, height = Inches(1), Inches(2.2), Inches(8), Inches(4)
            textbox = slide.shapes.add_textbox(left, top, width, height)
            tf = textbox.text_frame
            tf.word_wrap = True
            for line in description.split("\n"):
                if line.strip():
                    bullet = tf.add_paragraph()
                    bullet.text = line.strip()
                    bullet.font.size = Pt(22)
                    bullet.font.color.rgb = TEXT_DARK
                    bullet.level = 0

    prs.save(filename)
    return filename
