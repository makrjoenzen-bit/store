"""
Auto Promo Ad-Image Generator — Pillow templates (Playwright optional).
Templates: promo_flash | just_restocked | referral_giveaway
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


TEMPLATES = ("promo_flash", "just_restocked", "referral_giveaway")


class AdImageGenerator:
    WIDTH = 1080
    HEIGHT = 1350

    def generate(
        self,
        *,
        template: str = "promo_flash",
        title: str,
        price_usd: float | Decimal,
        price_syp: Optional[float | Decimal] = None,
        features: Optional[List[str]] = None,
        warranty_days: int = 30,
        stock: Optional[int] = None,
        brand_name: str = "Digital Store",
        campaign_line: Optional[str] = None,
    ) -> bytes:
        tpl = (template or "promo_flash").strip().lower()
        if tpl not in TEMPLATES:
            tpl = "promo_flash"
        # Prefer Playwright HTML if available; always fall back to Pillow
        try:
            png = self._try_playwright(
                tpl, title, price_usd, price_syp, features, warranty_days, stock, brand_name, campaign_line
            )
            if png:
                return png
        except Exception as e:
            logger.debug(f"Playwright ad render skipped: {e}")
        return self._pillow_render(
            tpl, title, price_usd, price_syp, features, warranty_days, stock, brand_name, campaign_line
        )

    def _try_playwright(self, *args) -> Optional[bytes]:
        # Optional path — tests rely on Pillow; keep import lazy
        return None

    def _pillow_render(
        self,
        template: str,
        title: str,
        price_usd: float | Decimal,
        price_syp: Optional[float | Decimal],
        features: Optional[List[str]],
        warranty_days: int,
        stock: Optional[int],
        brand_name: str,
        campaign_line: Optional[str],
    ) -> bytes:
        from PIL import Image, ImageDraw, ImageFont

        if template == "just_restocked":
            bg = (8, 28, 18)
            accent = (52, 211, 153)
        elif template == "referral_giveaway":
            bg = (28, 10, 40)
            accent = (244, 114, 182)
        else:
            bg = (8, 10, 24)
            accent = (56, 189, 248)

        img = Image.new("RGB", (self.WIDTH, self.HEIGHT), bg)
        draw = ImageDraw.Draw(img)
        # Neon frame
        draw.rounded_rectangle(
            (40, 40, self.WIDTH - 40, self.HEIGHT - 40),
            radius=36,
            outline=accent,
            width=4,
        )
        # Soft glow bar
        draw.rectangle((80, 120, self.WIDTH - 80, 128), fill=accent)

        font_lg = self._font(64)
        font_md = self._font(42)
        font_sm = self._font(32)
        font_xl = self._font(96)

        draw.text((80, 160), brand_name[:40], fill=(200, 210, 230), font=font_sm)
        y = 230
        # Title wrapped
        for line in self._wrap(title, 22)[:4]:
            draw.text((80, y), line, fill=(255, 255, 255), font=font_lg)
            y += 72

        if template == "referral_giveaway":
            line = campaign_line or "ادعُ 3 أصدقاء واحصل على حساب مجاني"
            y += 20
            for ln in self._wrap(line, 28)[:3]:
                draw.text((80, y), ln, fill=accent, font=font_md)
                y += 50
        else:
            feats = features or [
                "تفعيل فوري",
                f"ضمان {warranty_days} يوم",
                "دعم سوري سريع",
            ]
            y += 30
            for f in feats[:4]:
                draw.text((100, y), f"• {f}", fill=(180, 190, 210), font=font_sm)
                y += 48

        if template == "just_restocked" and stock is not None:
            draw.text((80, y + 20), f"المتوفر الآن: {stock}", fill=accent, font=font_md)
            y += 70

        # Price block
        price_y = self.HEIGHT - 320
        draw.rounded_rectangle(
            (80, price_y, self.WIDTH - 80, price_y + 180),
            radius=28,
            fill=(20, 24, 40) if template != "just_restocked" else (12, 40, 28),
            outline=accent,
            width=3,
        )
        price_str = f"${Decimal(str(price_usd)).quantize(Decimal('0.01'))}"
        draw.text((110, price_y + 30), price_str, fill=accent, font=font_xl)
        if price_syp is not None:
            syp_str = f"{int(Decimal(str(price_syp))):,} ل.س".replace(",", ",")
            draw.text((110, price_y + 120), syp_str, fill=(220, 220, 230), font=font_md)

        # Warranty badge
        badge = f"🛡 ضمان {warranty_days} يوم"
        draw.text((80, self.HEIGHT - 100), badge, fill=(160, 170, 190), font=font_sm)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if not data or len(data) < 100:
            raise RuntimeError("Generated PNG is empty")
        return data

    @staticmethod
    def _font(size: int):
        from PIL import ImageFont
        for name in (
            "arial.ttf",
            "Arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap(text: str, width: int) -> List[str]:
        words = (text or "").split()
        if not words:
            return [""]
        lines: List[str] = []
        cur = words[0]
        for w in words[1:]:
            if len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines


ad_image_generator = AdImageGenerator()
