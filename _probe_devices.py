import sounddevice as sd

for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        try:
            s = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32",
                blocksize=1600, device=i,
            )
            s.close()
            print(f"[{i}] OK    hostapi={sd.query_hostapis(d['hostapi'])['name']:12s} {d['name']}")
        except Exception as e:
            print(f"[{i}] FAIL  hostapi={sd.query_hostapis(d['hostapi'])['name']:12s} {d['name']} -> {e}")
