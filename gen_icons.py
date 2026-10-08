# ============================================================
# PWA ICON GENERATOR
# Ye script Smart Farming app ke logo/icon PNG files banata hai.
# Browser favicon aur mobile/PWA install icons isi se generate kiye ja sakte hain.
# ============================================================
from PIL import Image, ImageDraw, ImageChops, ImageFilter
import math

def leaf_mask(size, r):
    # Vesica-piscis (lens) shape from two overlapping circles, rotated for a leaf look
    m1 = Image.new("L", (size, size), 0)
    m2 = Image.new("L", (size, size), 0)
    d1, d2 = ImageDraw.Draw(m1), ImageDraw.Draw(m2)
    cx, cy = size/2, size/2
    offset = r*0.62
    ang = math.radians(45)
    ox, oy = offset*math.cos(ang), offset*math.sin(ang)
    d1.ellipse([cx-ox-r, cy-oy-r, cx-ox+r, cy-oy+r], fill=255)
    d2.ellipse([cx+ox-r, cy+oy-r, cx+ox+r, cy+oy+r], fill=255)
    return ImageChops.multiply(m1, m2)

def make_icon(size, maskable=False, path="icon.png"):
    img = Image.new("RGBA", (size, size), (0,0,0,0))
    pad = int(size*0.16) if maskable else int(size*0.04)
    bg_box = [pad, pad, size-pad, size-pad]
    radius = int((size-2*pad)*0.26)
    bg = Image.new("RGBA", (size, size), (0,0,0,0))
    bd = ImageDraw.Draw(bg)
    bd.rounded_rectangle(bg_box, radius=radius, fill="#14532d")
    # soft top-light gradient overlay
    grad = Image.new("L", (1, size), 0)
    for y in range(size):
        t = y/size
        v = int(255*(0.55*(1-t)))
        grad.putpixel((0,y), v)
    grad = grad.resize((size, size))
    light = Image.new("RGBA", (size, size), "#22c55e")
    light.putalpha(grad)
    bg = Image.alpha_composite(bg, Image.composite(light, Image.new("RGBA",(size,size),(0,0,0,0)), Image.new("L",(size,size),255)).convert("RGBA"))
    # re-clip to rounded rect
    clip = Image.new("L", (size, size), 0)
    ImageDraw.Draw(clip).rounded_rectangle(bg_box, radius=radius, fill=255)
    bg.putalpha(ImageChops.multiply(bg.split()[3], clip))

    r = size*0.235
    lm = leaf_mask(size, r)
    leaf = Image.new("RGBA", (size, size), "#ecfdf5")
    leaf.putalpha(lm)

    # vein line, clipped to the leaf's own mask so it never spills outside it
    vein_layer = Image.new("RGBA", (size, size), (0,0,0,0))
    vd = ImageDraw.Draw(vein_layer)
    cx, cy = size/2, size/2
    ang = math.radians(45)
    x1 = cx - r*0.95*math.cos(ang); y1 = cy - r*0.95*math.sin(ang)
    x2 = cx + r*0.95*math.cos(ang); y2 = cy + r*0.95*math.sin(ang)
    vd.line([x1,y1,x2,y2], fill="#166534", width=max(2,int(size*0.016)))
    vein_layer.putalpha(ImageChops.multiply(vein_layer.split()[3], lm))

    out = Image.alpha_composite(bg, leaf)
    out = Image.alpha_composite(out, vein_layer)
    out.save(path)

make_icon(192, False, "/home/claude/project/PlantRagini/static/icons/icon-192.png")
make_icon(512, False, "/home/claude/project/PlantRagini/static/icons/icon-512.png")
make_icon(512, True, "/home/claude/project/PlantRagini/static/icons/icon-512-maskable.png")
make_icon(180, False, "/home/claude/project/PlantRagini/static/icons/apple-touch-icon.png")
make_icon(32, False, "/home/claude/project/PlantRagini/static/icons/favicon-32.png")
make_icon(16, False, "/home/claude/project/PlantRagini/static/icons/favicon-16.png")
print("icons done")
