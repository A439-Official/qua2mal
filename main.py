import os
import yaml
import zipfile
import io
import fractions
import tqdm
import ujson as json
import argparse


def read_yaml(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f.read().replace(": !ScrollGroup", ":"))
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    except yaml.YAMLError as e:
        print(f"YAML error: {e}")
    return None


def convert_time(ms, chart, offset=0):
    ms += offset
    current_ms = 0.0
    for i in range(len(chart["time"]) - 1):
        cur = chart["time"][i]
        nxt = chart["time"][i + 1]
        bpm = cur["bpm"]
        beat_len = 60000.0 / bpm
        start = cur["beat"][0] + cur["beat"][1] / cur["beat"][2]
        end = nxt["beat"][0] + nxt["beat"][1] / nxt["beat"][2]
        section_beats = end - start
        section_ms = section_beats * beat_len
        if current_ms + section_ms > ms:
            remain = ms - current_ms
            beat = start + remain / beat_len
            frac = fractions.Fraction(beat % 1).limit_denominator()
            return [int(beat), frac.numerator, frac.denominator]
        current_ms += section_ms
    last = chart["time"][-1]
    bpm = last["bpm"]
    beat_len = 60000.0 / bpm
    beat = last["beat"][0] + last["beat"][1] / last["beat"][2]
    beat += (ms - current_ms) / beat_len
    frac = fractions.Fraction(beat % 1).limit_denominator()
    return [int(beat), frac.numerator, frac.denominator]


def get_bpm(chart, time):
    last = chart["time"][0]["bpm"]
    for t in chart["time"]:
        if t["origin"] > time:
            break
        last = t["bpm"]
    return last


def get_sv(data, time):
    last = 1
    for sv in data["SliderVelocities"]:
        if sv.get("StartTime", 0) > time:
            break
        last = sv.get("Multiplier", 0)
    return last


def process_qua(zf, qua_file, offset):
    with zf.open(qua_file) as f:
        data = yaml.safe_load(f.read().decode("utf-8").replace(": !ScrollGroup", ":"))

    chart = {
        "meta": {
            "creator": data["Creator"],
            "background": data["BackgroundFile"],
            "version": data["DifficultyName"],
            "song": {
                "artist": data["Artist"],
                "title": data["Title"],
            },
            "preview": data["SongPreviewTime"],
            "id": -1,
            "mode_ext": {"column": int(data["Mode"].replace("Keys", ""))},
        },
        "note": [],
        "time": [],
        "effect": [],
    }
    chart["note"].append(
        {
            "beat": [0, 0, 1],
            "column": 0,
            "type": 1,
            "sound": data["AudioFile"],
        }
    )

    base_bpm = data["TimingPoints"][0]["Bpm"]
    chart["time"].append(
        {
            "beat": [0, 0, 1],
            "bpm": base_bpm,
            "origin": 0,
        }
    )
    last_bpm = base_bpm
    for tp in tqdm.tqdm(data["TimingPoints"]):
        if tp["Bpm"] != last_bpm:
            chart["time"].append(
                {
                    "beat": convert_time(tp.get("StartTime", 0), chart, offset),
                    "bpm": tp["Bpm"],
                    "origin": tp.get("StartTime", 0),
                }
            )
        last_bpm = tp["Bpm"]
    chart["time"].sort(key=lambda x: x["beat"][0] + x["beat"][1] / x["beat"][2])

    for note in tqdm.tqdm(data["HitObjects"]):
        obj = {
            "beat": convert_time(note["StartTime"], chart, offset),
            "column": note["Lane"] - 1,
        }
        if "EndTime" in note:
            obj |= {
                "endbeat": convert_time(note["EndTime"], chart, offset),
                "type": 2,
            }
        chart["note"].append(obj)

    for t in tqdm.tqdm(chart["time"]):
        chart["effect"].append(
            {
                "beat": t["beat"],
                "sv": (base_bpm / t["bpm"]) * get_sv(data, t["origin"]),
            }
        )

    for sv in tqdm.tqdm(data["SliderVelocities"]):
        chart["effect"].append(
            {
                "beat": convert_time(sv.get("StartTime", 0), chart, offset),
                "sv": (base_bpm / get_bpm(chart, sv.get("StartTime", 0))) * sv.get("Multiplier", 0),
            }
        )

    return chart, data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--offset", type=int, default=32)
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output or (os.path.splitext(os.path.basename(input_path))[0] + ".mcz")

    offset = args.offset
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(input_path, "r") as zf:
        qua_files = [f for f in zf.namelist() if f.endswith(".qua")]

        if not qua_files:
            raise Exception("No .qua file found")

        added_files = set()

        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:

            for qua_file in qua_files:
                print(f"Processing {qua_file}")

                chart, data = process_qua(zf, qua_file, offset)

                name = os.path.splitext(os.path.basename(qua_file))[0]
                zout.writestr(
                    name + ".mc",
                    json.dumps(chart, indent=4).encode("utf-8"),
                )

                audio = data["AudioFile"]
                if audio not in added_files:
                    zout.writestr(audio, zf.read(audio))
                    added_files.add(audio)
                bg = data.get("BackgroundFile")
                if bg and bg not in added_files:
                    zout.writestr(bg, zf.read(bg))
                    added_files.add(bg)

    with open(output_path, "wb") as f:
        f.write(zip_buffer.getvalue())


if __name__ == "__main__":
    main()
