"""Chatterbox (yerel, MIT lisanslı) ile toplu seslendirme — AYRI Python ortamında koşar
(`work/tts-venv`; torch ağır, aracın kendi ortamına girmez). Hesap/kart/ağ ücreti yok
(kullanıcı, 2026-10-20: Google ön ödeme istedi → yerel). İlk koşuda modeller Hugging Face'ten iner.

    work/tts-venv/bin/python promokit/tts_chatterbox.py jobs.json

jobs.json: [{"text": "…", "out": "/mutlak/x.wav", "lang": "en", "exaggeration": 0.5, "cfg_weight": 0.5,
             "temperature": 0.8, "seed": 0, "prompt": null, "verify": {"model": "base.en", "tries": 4}}]
Model BİR kez yüklenir, işler sırayla yazılır (satır başına yeniden yüklemek dakikalar demek).
`seed` örneklemeyi sabitler → aynı satır aynı sesi verir (önbellek anahtarına girer).
`prompt`: ses klonlama için 5-10 sn'lik referans kaydı — yoksa modelin kendi sesi.

DOĞRULAMA (`verify`, faster-whisper): Chatterbox bazen cümleyi bitirip UYDURMAYA devam ediyor ya da
cümlenin bir parçasını TEKRARLIYOR (ölçüldü 2026-10-20: "Macs don't have a touchscreen." + 7 sn anlamsız
konuşma · "Now in open beta, now in open beta." · cümle sonrası 3 sn hırıltı). Kulakla dinlenmeden
fark edilmez → her deneme yazıya dökülür, metinle karşılaştırılır; tutmazsa sonraki tohumla yeniden
üretilir. Seçilenin yanına `<out>.json`: {seed, ok, ratio, heard, start, end} — `start/end` sözcük
zamanları, çağıran sesi oradan KIRPAR (baştaki sessizlik + sondaki kuyruk gider).
"""
import json
import os
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # MPS'te eksik işlemler CPU'ya düşsün

import torch  # noqa: E402
import torchaudio  # noqa: E402
from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asrcheck import judge  # noqa: E402  (bağımlılıksız; aracın kendi testleri de aynı modülü sınar)


def main(path: str):
    with open(path, encoding="utf-8") as f:
        jobs = json.load(f)
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model = ChatterboxMultilingualTTS.from_pretrained(device=dev)
    asr = {}
    for j in jobs:
        v = j.get("verify") or {}
        tries = max(1, int(v.get("tries", 1))) if v else 1
        rec = None
        if v:
            try:
                import librosa
                from faster_whisper import WhisperModel
                if v["model"] not in asr:
                    asr[v["model"]] = WhisperModel(v["model"], device="cpu", compute_type="int8")
                rec = asr[v["model"]]
            except ImportError:
                print("⚠️ faster-whisper yok — doğrulamasız (kurulum: tts-venv/bin/pip install faster-whisper)",
                      file=sys.stderr, flush=True)
                tries = 1
        cands = []
        for k in range(tries):
            seed = int(j.get("seed", 0)) + k
            torch.manual_seed(seed)
            wav = model.generate(j["text"], language_id=j.get("lang", "tr"), audio_prompt_path=j.get("prompt"),
                                 exaggeration=float(j.get("exaggeration", 0.5)), cfg_weight=float(j.get("cfg_weight", 0.5)),
                                 temperature=float(j.get("temperature", 0.8)))
            out = j["out"] if k == 0 else f"{j['out']}.{k}.wav"
            torchaudio.save(out, wav.cpu(), model.sr)
            if rec is None:
                cands.append((out, {"seed": seed, "ok": True, "verified": False}))
                break
            a, _ = librosa.load(out, sr=16000)     # dizi ver: faster-whisper'ın dosya çözücüsü bu PyAV ile uyumsuz
            segs, _ = rec.transcribe(a, language=j.get("lang", "tr"), word_timestamps=True, beam_size=5)
            words = [(w.word, float(w.start), float(w.end)) for s in segs for w in (s.words or [])]
            rep = {"seed": seed, "verified": True, **judge(j["text"], words)}
            cands.append((out, rep))
            print(f"  deneme {k + 1}: {'✓' if rep['ok'] else '✗'} {rep['ratio']:.2f} «{rep['heard']}»",
                  file=sys.stderr, flush=True)
            if rep["ok"]:
                break
        # ilk tutan; hiçbiri tutmazsa en benzer ön ek (kuyruğu kırpılarak kullanılır, çağıran uyarır)
        out, rep = next((c for c in cands if c[1]["ok"]), max(cands, key=lambda c: c[1].get("ratio", 0)))
        if out != j["out"]:
            os.replace(out, j["out"])
        for o, _ in cands:
            if o != j["out"] and os.path.exists(o):
                os.remove(o)
        rep["tries"] = len(cands)
        with open(j["out"] + ".json", "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
        print(j["out"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
