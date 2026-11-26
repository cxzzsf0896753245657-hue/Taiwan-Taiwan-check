"""Replace Capacitor's default launcher icons with the 避雷查 icon."""
import pathlib, shutil
from PIL import Image

res = pathlib.Path("android/app/src/main/res")
src = Image.open("../site/icons/icon-512.png").convert("RGB")
sizes = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
for dpi, px in sizes.items():
    d = res / f"mipmap-{dpi}"
    d.mkdir(parents=True, exist_ok=True)
    img = src.resize((px, px), Image.LANCZOS)
    for name in ("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"):
        img.save(d / name)
shutil.rmtree(res / "mipmap-anydpi-v26", ignore_errors=True)
print("icons replaced")
