"""Generate the original Offline Writing Reviser application icon."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    output = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "offline_writing_reviser"
        / "assets"
        / "offline-writing-reviser.ico"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (23, 74, 126, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((12, 12, 243, 243), radius=48, fill=(23, 74, 126, 255))
    for y, end_x in ((72, 184), (124, 164), (176, 136)):
        draw.rounded_rectangle((58, y, end_x, y + 18), radius=8, fill="white")
    draw.line((164, 179, 190, 205, 224, 159), fill=(127, 209, 174), width=18)
    image.save(
        output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(output)


if __name__ == "__main__":
    main()
