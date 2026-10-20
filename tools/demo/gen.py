"""Demo library: made-up artists, generated tones instead of music, generated cover art. Nothing here is real music."""
import io, math, random, struct, wave, sys
from pathlib import Path
from PIL import Image, ImageDraw
from mutagen.id3 import APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.wave import WAVE

root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
CATALOG = [
  ("Neon Harbor", "City Lights", 2023, "Synthwave", ((255, 70, 140), (60, 20, 140)), ["Afterglow", "Midnight Signal", "Glass Skyline", "Overpass", "Violet Rain", "Last Train to Pier 9"]),
  ("Neon Harbor", "Midnight Drive", 2021, "Synthwave", ((20, 200, 220), (20, 30, 100)), ["Dashboard Glow", "Open Road", "Tunnel Vision", "Fuel Light"]),
  ("Velvet Static", "Soft Machines", 2022, "Indie", ((250, 190, 90), (150, 50, 70)), ["Paper Moon", "Soft Machines", "Static Bloom", "Apartment 4B", "Dust and Honey", "Tiny Orchestras"]),
  ("Paper Lanterns", "Slow Bloom", 2020, "Folk", ((140, 200, 120), (20, 90, 80)), ["Morning Fields", "River Song", "Slow Bloom", "Porch Light", "Wool and Rain"]),
  ("Orbit Kids", "Moonbase", 2024, "Electronic", ((150, 120, 255), (15, 15, 60)), ["Countdown", "Low Gravity", "Moonbase", "Signal Lost", "Re-entry"]),
  ("The Quiet Engines", "Long Way Home", 2019, "Rock", ((235, 110, 60), (60, 20, 20)), ["Gravel Roads", "Long Way Home", "Engine Room", "Hold the Line", "Ten Miles of Rain", "Headlights"]),
  ("Mira Solis", "Golden Hour", 2022, "Pop", ((255, 210, 120), (230, 90, 120)), ["Golden Hour", "Sunday Feeling", "Ocean Eyes Open", "Say It Slow", "Waiting for Summer"]),
]
rnd = random.Random(7)

def cover(c1, c2, seed):
    r = random.Random(seed); n = 600
    img = Image.new("RGB", (n, n))
    px = img.load()
    for y in range(n):
        for x in range(n):
            t = (x * 0.35 + y * 0.65) / n
            px[x, y] = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(7):
        cx, cy, rad = r.randint(0, n), r.randint(0, n), r.randint(60, 240)
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(255, 255, 255, r.randint(14, 42)))
    for k in range(5):
        y = 360 + k * 34
        d.line((40, y, n - 40, y + r.randint(-40, 40)), fill=(255, 255, 255, 60), width=3)
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()

def tone(path, seconds, freq):
    import numpy as np
    rate = 8000
    t = np.arange(int(rate * seconds)) / rate
    data = (128 + 40 * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t))).astype(np.uint8)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate); w.writeframes(data.tobytes())

for ai, (artist, album, year, genre, cols, titles) in enumerate(CATALOG):
    art = cover(*cols, seed=ai)
    for n, title in enumerate(titles, 1):
        p = root / artist / f"{album} ({year})" / f"{n:02d} - {title}.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        tone(p, rnd.randint(158, 252), 110 + 20 * n)
        f = WAVE(str(p)); f.add_tags()
        for frame, val in ((TIT2, title), (TPE1, artist), (TALB, album), (TPE2, artist), (TRCK, f"{n}/{len(titles)}"), (TCON, genre), (TDRC, str(year))):
            f.tags.add(frame(encoding=3, text=val))
        f.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=art)); f.save()
print("tracks:", len(list(root.rglob("*.wav"))))
