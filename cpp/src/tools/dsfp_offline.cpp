// dsfp_offline: run the C++ DSP on a raw float32 mono file, mirroring the Python functions so
// tests/test_cpp_port.py can compare the two implementations.
//
//   dsfp_offline yin     <in.f32> <frames.csv> --sr 44100 [--hop 256]
//       like dsfp_tune.analysis.yin_track       -> csv: f0,aperiodicity,voiced
//   dsfp_offline shift   <in.f32> <out.f32>    --sr 44100 --ratio 1.06 [--n-fft 2048 --overlap 4]
//       like dsfp_tune.synthesis.freq_shift     -> float32, same length, no delay
//   dsfp_offline correct <in.f32> <out.f32>    --sr 44100 [--frames f.csv] [--block 512] [options]
//       like dsfp_tune.correction.correct_pitch -> float32, same length, no delay;
//       frames csv: f0,voiced,target_midi,correction (one row per pitch estimate)
//       options: --retune-ms --release-ms --amount --hysteresis --hold-ms --note-window-ms
//                --note-jump --max-correction --key "<tonic 0-11>:<scale id>" --n-fft --overlap --hop
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

#include "dsp/FrequencyShifter.h"
#include "dsp/PitchCorrector.h"
#include "dsp/Yin.h"

namespace {

std::vector<double> loadF32(const std::string& path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("cannot read " + path);
    const auto bytes = static_cast<size_t>(f.tellg());
    f.seekg(0);
    std::vector<float> tmp(bytes / sizeof(float));
    f.read(reinterpret_cast<char*>(tmp.data()), static_cast<std::streamsize>(tmp.size() * sizeof(float)));
    return std::vector<double>(tmp.begin(), tmp.end());
}

void saveF32(const std::string& path, const std::vector<double>& x) {
    std::vector<float> tmp(x.begin(), x.end());
    std::ofstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot write " + path);
    f.write(reinterpret_cast<const char*>(tmp.data()), static_cast<std::streamsize>(tmp.size() * sizeof(float)));
}

std::map<std::string, std::string> parseOptions(int argc, char** argv, int start) {
    std::map<std::string, std::string> opts;
    for (int i = start; i < argc; ++i) {
        std::string a = argv[i];
        if (a.rfind("--", 0) != 0 || i + 1 >= argc) throw std::runtime_error("bad option " + a);
        opts[a.substr(2)] = argv[++i];
    }
    return opts;
}

double num(const std::map<std::string, std::string>& o, const std::string& k, double def) {
    auto it = o.find(k);
    return it == o.end() ? def : std::stod(it->second);
}

struct FrameLog {
    std::vector<dsfp::CorrectorStatus> rows;
    static void cb(void* ctx, const dsfp::CorrectorStatus& s) { static_cast<FrameLog*>(ctx)->rows.push_back(s); }
};

int runYin(const std::vector<double>& x, const std::string& csv, const std::map<std::string, std::string>& o) {
    const double sr = num(o, "sr", 44100);
    const int hop = static_cast<int>(num(o, "hop", 256));
    dsfp::Yin yin(sr, num(o, "f-min", 70), num(o, "f-max", 1000));
    const int n = yin.frameLength();
    const long len = static_cast<long>(x.size());
    const long nFrames = std::max(1L, static_cast<long>(std::ceil(std::max(len - n, 0L) / static_cast<double>(hop))) + 1);
    std::vector<double> padded(static_cast<size_t>((nFrames - 1) * hop + n), 0.0);
    std::copy(x.begin(), x.end(), padded.begin());
    std::ofstream f(csv);
    f.precision(17);
    f << "f0,aperiodicity,voiced\n";
    for (long i = 0; i < nFrames; ++i) {
        const auto e = yin.detect(padded.data() + i * hop);
        f << e.f0 << "," << e.aperiodicity << "," << (e.voiced ? 1 : 0) << "\n";
    }
    return 0;
}

int runShift(const std::vector<double>& x, const std::string& out, const std::map<std::string, std::string>& o) {
    saveF32(out, dsfp::freqShift(x, num(o, "ratio", 1.0), static_cast<int>(num(o, "n-fft", 2048)),
                                 static_cast<int>(num(o, "overlap", 4))));
    return 0;
}

int runCorrect(const std::vector<double>& x, const std::string& out, const std::map<std::string, std::string>& o) {
    const double sr = num(o, "sr", 44100);
    dsfp::PitchCorrector pc(sr, static_cast<int>(num(o, "n-fft", 2048)), static_cast<int>(num(o, "overlap", 4)),
                            static_cast<int>(num(o, "hop", 256)), num(o, "f-min", 70), num(o, "f-max", 1000));
    dsfp::CorrectorParams p;
    p.retuneMs = num(o, "retune-ms", 25);
    p.releaseMs = num(o, "release-ms", p.retuneMs);
    p.amount = num(o, "amount", 1);
    p.hysteresis = num(o, "hysteresis", 0.15);
    p.holdMs = num(o, "hold-ms", 60);
    p.noteWindowMs = num(o, "note-window-ms", 120);
    p.noteJump = num(o, "note-jump", 0.75);
    p.maxCorrection = num(o, "max-correction", 2);
    if (auto it = o.find("key"); it != o.end()) {
        const auto colon = it->second.find(':');
        const int tonic = std::stoi(it->second.substr(0, colon));
        const std::string scale = it->second.substr(colon + 1);
        int idx = -1;
        for (size_t i = 0; i < dsfp::scales().size(); ++i)
            if (scale == dsfp::scales()[i].id) idx = static_cast<int>(i);
        if (idx < 0) throw std::runtime_error("unknown scale " + scale);
        p.allowed = dsfp::keyPitchClasses(tonic, idx);
    }
    pc.setParams(p);
    FrameLog log;
    pc.setEstimateCallback(&FrameLog::cb, &log);

    const int lat = pc.latency();
    const int block = static_cast<int>(num(o, "block", 512));
    std::vector<double> in(x);
    in.resize(x.size() + static_cast<size_t>(lat), 0.0);
    std::vector<double> y(in.size());
    for (size_t pos = 0; pos < in.size(); pos += static_cast<size_t>(block)) {
        const int n = static_cast<int>(std::min(in.size() - pos, static_cast<size_t>(block)));
        pc.process(in.data() + pos, y.data() + pos, n);
    }
    saveF32(out, std::vector<double>(y.begin() + lat, y.begin() + lat + static_cast<long>(x.size())));

    if (auto it = o.find("frames"); it != o.end()) {
        std::ofstream f(it->second);
        f.precision(17);
        f << "f0,voiced,target_midi,correction\n";
        for (const auto& s : log.rows)
            f << s.f0 << "," << (s.voiced ? 1 : 0) << "," << s.targetMidi << "," << s.correction << "\n";
    }
    std::cerr << "latency " << lat << " samples, " << log.rows.size() << " estimates\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 4) {
        std::cerr << "usage: dsfp_offline yin|shift|correct <in.f32> <out> [--options]\n";
        return 2;
    }
    try {
        const std::string mode = argv[1];
        const auto x = loadF32(argv[2]);
        const auto opts = parseOptions(argc, argv, 4);
        if (mode == "yin") return runYin(x, argv[3], opts);
        if (mode == "shift") return runShift(x, argv[3], opts);
        if (mode == "correct") return runCorrect(x, argv[3], opts);
        std::cerr << "unknown mode " << mode << "\n";
        return 2;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
