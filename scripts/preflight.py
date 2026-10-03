"""Calistirmadan once: sistem kendi icinde tutarli mi, servisler ayakta mi.

1 Ekim gecesi uc sey ayri ayri patladi ve ucu de SESSIZDI:

  1. Commit edilmemis is `git checkout` ile silindi. Hicbir hata vermedi.
  2. Model 24 feature'likti, config 21 diyordu. API "degraded" dedi ama
     hangisinin uyusmadigini soylemedi; yarim saat yanlis yerde arandi.
  3. LM Studio'da model yuklu degildi, ustelik bir embedding modeline sohbet
     modelinin adi takilmisti - /v1/models listesinde dogru gorunuyordu.

Ucu de bes saniyede yakalanabilirdi. Bu dosya onu yapiyor.

Her kontrol ucundan birini doner:
  TAMAM   - gecti
  DUSTU   - bozuk, calistirmadan once duzeltilmeli (cikis kodu 1)
  UYARI   - calismayi engellemiyor ama bilmen gereken bir sey

    python scripts/preflight.py                 temel kontroller
    python scripts/preflight.py --servis        ayakta olmasi gereken servisler
    python scripts/preflight.py --llm           yerel modelde GERCEK cikarim denemesi
    python scripts/preflight.py --hepsi
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

KIRMIZI, YESIL, SARI, MAVI, SIFIR = "\033[0;31m", "\033[0;32m", "\033[0;33m", "\033[0;34m", "\033[0m"
if not sys.stdout.isatty():
    KIRMIZI = YESIL = SARI = MAVI = SIFIR = ""

DUSEN: list[str] = []
UYARILAR: list[str] = []


def tamam(baslik, detay=""):
    print(f"  {YESIL}✓{SIFIR} {baslik}" + (f" — {detay}" if detay else ""))


def dustu(baslik, detay, nasil=""):
    print(f"  {KIRMIZI}✗{SIFIR} {baslik} — {detay}")
    if nasil:
        print(f"      {MAVI}→{SIFIR} {nasil}")
    DUSEN.append(baslik)


def uyar(baslik, detay, nasil=""):
    print(f"  {SARI}!{SIFIR} {baslik} — {detay}")
    if nasil:
        print(f"      {MAVI}→{SIFIR} {nasil}")
    UYARILAR.append(baslik)


def bolum(ad):
    print(f"\n{ad}")


# --- 1. Model <-> config ------------------------------------------------------

def kontrol_feature_seti():
    """1 Ekim arizasinin ta kendisi: model 24 feature, config 21.

    Sirayi da karsilastiriyoruz: CatBoost'a feature'lar sirayla veriliyor, ayni
    kumeyi farkli sirada vermek sessizce yanlis skor uretir - 'eksik kolon yok'
    diyen bir kontrol bunu kacirirdi.
    """
    try:
        from config import FEATURES
    except Exception as exc:
        dustu("config.py", f"okunamadi: {exc}", "config.py'de sozdizimi hatasi ya da eksik import var")
        return
    meta_yolu = KOK / "saved_models" / "model_meta.json"
    if not meta_yolu.exists():
        dustu("model_meta.json", "yok", "model hic egitilmemis: python running_train_pipeline.py")
        return
    try:
        meta = json.loads(meta_yolu.read_text(encoding="utf-8"))
    except Exception as exc:
        dustu("model_meta.json", f"bozuk JSON: {exc}", "yeniden egit")
        return

    model_f = list(meta.get("features") or [])
    config_f = list(FEATURES)
    if model_f == config_f:
        tamam("model ile config ayni feature setinde",
              f"{len(model_f)} feature, egitim {meta.get('trained_at', 'tarih yok')}")
        return

    eksik = [f for f in config_f if f not in model_f]
    fazla = [f for f in model_f if f not in config_f]
    if not eksik and not fazla:
        detay = f"ayni kolonlar ama FARKLI SIRA (model {len(model_f)}, config {len(config_f)})"
    else:
        parcalar = [f"model {len(model_f)} / config {len(config_f)}"]
        if fazla:
            parcalar.append(f"modelde olup config'de olmayan: {fazla}")
        if eksik:
            parcalar.append(f"config'de olup modelde olmayan: {eksik}")
        detay = " | ".join(parcalar)
    dustu("model ile config UYUSMUYOR", detay,
          "ya modeli yeniden egit ya da dogru model dosyalarini geri getir. "
          "API bu durumda 'degraded' der ama sebebini soylemez.")


def kontrol_egitim_verisi():
    """Egitim verisinin kolonlari da feature setiyle uyusmali."""
    try:
        from config import FEATURES, TRAIN_DATA_PATH
    except Exception:
        return
    yol = Path(TRAIN_DATA_PATH)
    if not yol.exists():
        uyar("egitim verisi", f"{yol.name} yok", "yeniden egitim gerekirse bu dosya lazim")
        return
    with open(yol, encoding="utf-8") as f:
        basliklar = f.readline().strip().split(",")
    eksik = [c for c in FEATURES if c not in basliklar]
    if eksik:
        dustu("egitim verisi feature setiyle uyusmuyor", f"dosyada olmayan: {eksik}",
              f"{yol.name} bayat - B-21 gibi bir feature degisikliginden sonra yeniden uretilmeli")
    else:
        tamam("egitim verisi feature setini karsiliyor", f"{len(basliklar)} kolon")


# --- 2. Git ------------------------------------------------------------------

def kontrol_git():
    """Commit edilmemis is, bir `git checkout` uzaklikta yok olur. 1 Ekim'de oldu."""
    try:
        sonuc = subprocess.run(["git", "status", "--porcelain"], cwd=KOK,
                               capture_output=True, text=True, timeout=10)
    except Exception as exc:
        uyar("git", f"calistirilamadi: {exc}")
        return
    if sonuc.returncode != 0:
        uyar("git", "burasi bir git deposu degil", "retention_app hala versiyonsuzsa: L-01")
        return
    satirlar = [s for s in sonuc.stdout.splitlines() if s.strip()]
    if not satirlar:
        tamam("git calisma agaci temiz")
        return
    izlenmeyen = [s for s in satirlar if s.startswith("??")]
    degisen = [s for s in satirlar if not s.startswith("??")]
    uyar("commit edilmemis is var",
         f"{len(degisen)} degisen, {len(izlenmeyen)} izlenmeyen dosya",
         "branch degistirmeden once commit et - commit edilmemis is bir "
         "`git checkout` ile silinir ve hicbir uyari vermez")


# --- 3. Servisler ------------------------------------------------------------

def _get(url, baslik=None, saniye=4):
    istek = urllib.request.Request(url, headers=baslik or {})
    with urllib.request.urlopen(istek, timeout=saniye) as cevap:
        return cevap.status, json.loads(cevap.read().decode("utf-8"))


def kontrol_servisler(api_url, api_key, retention_url):
    """Servis ayakta mi DEGIL - dogru cevap veriyor mu."""
    try:
        durum, govde = _get(f"{api_url}/health")
    except Exception as exc:
        dustu("churn API", f"{api_url} cevap vermiyor ({exc.__class__.__name__})",
              f"Terminal 1: API_KEY=... DATA_SOURCE=csv python -m uvicorn api.main:app --port {api_url.rsplit(':', 1)[-1]}")
        return
    bilesenler = govde.get("components", {})
    bozuk = [ad for ad, iyi in bilesenler.items() if not iyi]
    if bozuk:
        dustu("churn API hazir degil", f"yuklenmeyen: {bozuk}",
              "neredeyse her zaman model/config uyusmazligi - yukaridaki 1. kontrole bak")
    else:
        tamam("churn API hazir", f"{len(bilesenler)} bilesen")

    if api_key:
        try:
            durum, govde = _get(f"{api_url}/students?threshold=0", {"X-API-Key": api_key})
            sayi = govde.get("count", 0)
            if sayi:
                tamam("GET /students", f"{sayi} ogrenci skorlandi")
            else:
                uyar("GET /students", "0 ogrenci dondu", "gunluk veri dosyasi bos olabilir")
        except urllib.error.HTTPError as exc:
            dustu("GET /students", f"HTTP {exc.code}",
                  "401 ise API anahtari uyusmuyor" if exc.code == 401 else "")
        except Exception as exc:
            dustu("GET /students", str(exc))

        try:
            _, govde = _get(f"{api_url}/campaigns", {"X-API-Key": api_key})
            sayi = govde.get("count", 0)
            if sayi:
                tamam("kayitli kampanya", f"{sayi} adet")
            else:
                uyar("kayitli kampanya", "0 adet",
                     "kampanya uretilmemis - panoda bos panel gorunur")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                uyar("GET /campaigns", "uc yok", "kampanya uclari bu surumde olmayabilir")
            else:
                dustu("GET /campaigns", f"HTTP {exc.code}")
        except Exception as exc:
            # Hata tipini yaziyoruz: once burada genel bir "okunamadi" vardi ve
            # kendi cagri hatami yutup beni yanlis yere baktirdi.
            dustu("GET /campaigns", f"{exc.__class__.__name__}: {exc}")

    if retention_url:
        try:
            _, govde = _get(f"{retention_url}/api/customers")
            kaynak = govde.get("source")
            sayi = len(govde.get("customers", []))
            if kaynak == "churn_api" and sayi:
                tamam("retention app", f"kaynak {kaynak}, {sayi} ogrenci")
            elif kaynak == "fixture":
                uyar("retention app", "kaynak 'fixture'",
                     "RETENTION_STUDENT_SOURCE=api uvicorn'un calistigi terminalde verilmemis")
            else:
                uyar("retention app", f"kaynak {kaynak}, {sayi} ogrenci")
        except Exception as exc:
            dustu("retention app", f"{retention_url} cevap vermiyor ({exc.__class__.__name__})",
                  "Terminal 2'yi export blogu ile baslat")


# --- 4. Yerel model ----------------------------------------------------------

def kontrol_llm(base_url, model):
    """`/v1/models` listesi YETMEZ.

    1 Ekim'de model listede gorunuyordu ama yuklu degildi; ustelik o isim bir
    embedding modeline takma ad olarak verilmisti. Tek dogru kontrol gercek bir
    cikarim istegi gondermek.
    """
    try:
        _, govde = _get(f"{base_url}/models", saniye=6)
    except Exception as exc:
        dustu("yerel model sunucusu", f"{base_url} cevap vermiyor ({exc.__class__.__name__})",
              "LM Studio acik mi")
        return
    idler = [m.get("id") for m in govde.get("data", [])]
    if model not in idler:
        dustu("model listede yok", f"{model!r} bulunamadi", f"listedekiler: {idler}")
        return
    tamam("model listede gorunuyor", model)

    yuk = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": 'Sadece bu JSON u don: {"ok":1}'}],
        "stream": False, "max_tokens": 16,
    }).encode("utf-8")
    istek = urllib.request.Request(f"{base_url}/chat/completions", data=yuk,
                                   headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(istek, timeout=120) as cevap:
            json.loads(cevap.read().decode("utf-8"))
        tamam("GERCEK cikarim calisti", "model bellege yuklu")
    except urllib.error.HTTPError as exc:
        mesaj = ""
        try:
            mesaj = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        if "No models loaded" in mesaj or "Failed to load" in mesaj:
            dustu("model YUKLU DEGIL", mesaj[:160],
                  f'"$HOME/.lmstudio/bin/lms" load {model} --context-length 8192 --parallel 1')
        else:
            dustu("cikarim reddedildi", f"HTTP {exc.code}: {mesaj[:160]}")
    except Exception as exc:
        dustu("cikarim denemesi basarisiz", str(exc)[:160])


def main():
    ayrist = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    ayrist.add_argument("--servis", action="store_true", help="servisler de kontrol edilsin")
    ayrist.add_argument("--llm", action="store_true", help="yerel modelde gercek cikarim denensin")
    ayrist.add_argument("--hepsi", action="store_true")
    ayrist.add_argument("--api-url", default=os.environ.get("PREFLIGHT_API_URL", "http://127.0.0.1:8000"))
    ayrist.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    ayrist.add_argument("--retention-url", default=os.environ.get("PREFLIGHT_RETENTION_URL", "http://127.0.0.1:8001"))
    ayrist.add_argument("--llm-url", default=os.environ.get("RETENTION_LOCAL_LLM_BASE_URL", "http://localhost:1234/v1"))
    ayrist.add_argument("--llm-model", default=os.environ.get("RETENTION_LOCAL_LLM_MODEL", ""))
    a = ayrist.parse_args()
    servis = a.servis or a.hepsi
    llm = a.llm or a.hepsi

    print(f"\n{MAVI}EO-Churn preflight{SIFIR}  —  {KOK}")

    bolum("1) Model, config ve veri tutarli mi")
    kontrol_feature_seti()
    kontrol_egitim_verisi()

    bolum("2) Is kaybolmaya acik mi")
    kontrol_git()

    if servis:
        bolum("3) Servisler")
        kontrol_servisler(a.api_url.rstrip("/"), a.api_key, a.retention_url.rstrip("/"))

    if llm:
        bolum("4) Yerel model")
        if not a.llm_model:
            uyar("yerel model", "model adi verilmedi",
                 "--llm-model ya da RETENTION_LOCAL_LLM_MODEL")
        else:
            kontrol_llm(a.llm_url.rstrip("/"), a.llm_model)

    print()
    if DUSEN:
        print(f"{KIRMIZI}{len(DUSEN)} kontrol dustu:{SIFIR} " + ", ".join(DUSEN))
        if UYARILAR:
            print(f"{SARI}{len(UYARILAR)} uyari:{SIFIR} " + ", ".join(UYARILAR))
        print("Duzeltmeden calistirma.\n")
        return 1
    if UYARILAR:
        print(f"{SARI}{len(UYARILAR)} uyari:{SIFIR} " + ", ".join(UYARILAR))
        print("Engelleyici bir sey yok.\n")
        return 0
    print(f"{YESIL}Hepsi tamam.{SIFIR}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
