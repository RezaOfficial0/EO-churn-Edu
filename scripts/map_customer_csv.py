"""Musterinin ham disa aktarimini EO-Churn'un kanonik semasina cevirir.

Neden ayri bir adim: VERI_TALEBI.md musteriye "kolon adlariniz bizimkiyle ayni
olmak zorunda degil, eslemeyi biz yapariz" diyor. O vaadin karsiligi bu betik.
Esleme MUSTERIYE OZEL bir JSON dosyasinda durur; cekirdek kod her musteride
aynidir. Yeni bir musteri = yeni bir column_map.json, kod degisikligi degil.

    python scripts/map_customer_csv.py \
        --in  musteri_export.csv \
        --map customers/demo_edu/column_map.json \
        --out data/updated_data.csv

Betik veriyi TEMIZLEMEZ, sadece ADLANDIRIR ve degerleri cevirir. Eksik deger,
aykiri deger ve kullanilamaz satir, boru hattinin kendi kapilarinin isi;
burada susturulurlarsa o kapilar bir daha hicbir seyi yakalayamaz.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from config import STUDENT_INFO, TARGET_FEATURE  # noqa: E402
from src.data.features import RAW_FEATURE_COLUMNS  # noqa: E402


class EslemeHatasi(Exception):
    """Esleme uygulanamadi. Mesaj, hangi kolonun eksik oldugunu soyler."""


def esleme_yukle(yol: Path) -> dict:
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EslemeHatasi(f"Esleme dosyasi yok: {yol}")
    except json.JSONDecodeError as e:
        raise EslemeHatasi(f"Esleme dosyasi gecerli JSON degil: {yol} ({e})")
    if "columns" not in veri:
        raise EslemeHatasi(f"Esleme dosyasinda 'columns' anahtari yok: {yol}")
    return veri


def uygula(ham: pd.DataFrame, esleme: dict, *, hedef_gerekli: bool) -> pd.DataFrame:
    kolonlar = esleme["columns"]

    # 1) Musterinin dosyasinda esleme dosyasinin bekledigi basliklar var mi.
    #    Bir basligin eksik olmasi sessiz gecilemez: o kolon kanonik seman da
    #    eksik kalir ve hata, kolon adi yerine "model yuklenemedi" gibi cok
    #    sonra ve cok uzakta bir yerde cikar.
    beklenen = set(kolonlar)
    gelen = set(ham.columns)
    eksik_baslik = sorted(beklenen - gelen)
    if eksik_baslik:
        raise EslemeHatasi(
            "Esleme dosyasindaki su basliklar musterinin dosyasinda yok: "
            + ", ".join(eksik_baslik)
            + ". Basliklar degismis olabilir; column_map.json'u guncelleyin."
        )

    df = ham.rename(columns=kolonlar)

    # 2) Deger cevrimleri: "ayrildi"/"aktif" -> 1/0 gibi.
    for kolon, harita in (esleme.get("value_maps") or {}).items():
        if kolon not in df.columns:
            continue
        cevrilmis = df[kolon].map(harita)
        taninmayan = df.loc[cevrilmis.isna() & df[kolon].notna(), kolon].unique()
        if len(taninmayan):
            raise EslemeHatasi(
                f"'{kolon}' kolonunda taninmayan deger(ler): {list(taninmayan)[:5]}. "
                f"column_map.json -> value_maps['{kolon}'] icine ekleyin."
            )
        df[kolon] = cevrilmis

    # 3) Musterinin bizimle ilgisi olmayan kolonlari. Adiyla dusurulur:
    #    "bilmedigimiz bir kolonu sessizce atmak" ile "bu kolonu gormezden
    #    gelmeye karar verdik" ayri seylerdir, ve ikincisi yazili olmali.
    dusur = [k for k in (esleme.get("drop") or []) if k in df.columns]
    df = df.drop(columns=dusur)

    # 4) Esleme dosyasinda adi hic gecmeyen kolon kaldiysa, bu bir surpriz.
    #    Sessizce birakilirsa boru hattinin cerceve kapisinda, musterinin
    #    anlamayacagi bir mesajla patlar.
    bilinen = set(kolonlar.values()) | set(dusur)
    surpriz = [k for k in df.columns if k not in bilinen]
    if surpriz:
        raise EslemeHatasi(
            "Musterinin dosyasinda esleme dosyasinda adi gecmeyen kolon(lar) var: "
            + ", ".join(surpriz)
            + ". column_map.json icinde ya 'columns' ile eslestirin ya 'drop' ile "
            "bilerek dusurun."
        )

    gerekli = list(STUDENT_INFO) + list(RAW_FEATURE_COLUMNS)
    if hedef_gerekli:
        gerekli.append(TARGET_FEATURE)
    eksik = [k for k in gerekli if k not in df.columns]
    if eksik:
        raise EslemeHatasi(
            "Esleme sonrasi su kanonik kolonlar hala eksik: " + ", ".join(eksik)
        )

    sira = [k for k in gerekli if k in df.columns]
    sira += [k for k in df.columns if k not in sira]
    return df[sira]


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--in", dest="girdi", required=True)
    a.add_argument("--map", dest="esleme", required=True)
    a.add_argument("--out", dest="cikti", required=True)
    a.add_argument("--no-target", action="store_true",
                   help="gunluk skorlama girdisi: 'churn' kolonu beklenmez")
    n = a.parse_args()

    ham = pd.read_csv(n.girdi)
    try:
        df = uygula(ham, esleme_yukle(Path(n.esleme)), hedef_gerekli=not n.no_target)
    except EslemeHatasi as e:
        print(f"ESLEME BASARISIZ: {e}", file=sys.stderr)
        return 1

    Path(n.cikti).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(n.cikti, index=False)
    print(f"{len(df)} satir, {len(df.columns)} kolon -> {n.cikti}")
    if TARGET_FEATURE in df.columns:
        oran = df[TARGET_FEATURE].mean()
        print(f"  {TARGET_FEATURE}: %{oran * 100:.1f} ({int(df[TARGET_FEATURE].sum())} kayit)")
    bos = df.isna().sum()
    bos = bos[bos > 0]
    if len(bos):
        print("  bos hucre:", ", ".join(f"{k}={v}" for k, v in bos.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
