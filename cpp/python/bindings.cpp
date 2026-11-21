// Python bindings for the C++ DSP (cpp/src/dsp), imported as dsfp_tune._native.
//
// The Python classes Yin (dsfp_tune/analysis/yin.py) and FrequencyShifter
// (dsfp_tune/synthesis/freq_shift.py) use these when the module is built, and fall back to
// their NumPy code otherwise. Argument checks stay in Python; this layer only moves arrays.
#include <cmath>
#include <limits>
#include <tuple>

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include "dsp/FrequencyShifter.h"
#include "dsp/Yin.h"

namespace py = pybind11;
using Array = py::array_t<double, py::array::c_style | py::array::forcecast>;

PYBIND11_MODULE(_native, m) {
    m.doc() = "C++ DSP kernels for dsfp_tune (YIN pitch detection, phase-vocoder frequency shifter)";

    py::class_<dsfp::Yin>(m, "Yin")
        .def(py::init([](double sampleRate, double fMin, double fMax, int window, double threshold,
                         py::object silenceDb) {
                 const double db = silenceDb.is_none() ? std::numeric_limits<double>::quiet_NaN()
                                                       : silenceDb.cast<double>();
                 return new dsfp::Yin(sampleRate, fMin, fMax, window, threshold, db);
             }),
             py::arg("sample_rate"), py::arg("f_min") = 70.0, py::arg("f_max") = 1000.0, py::arg("window") = 0,
             py::arg("threshold") = 0.1, py::arg("silence_db") = -60.0)
        .def_property_readonly("frame_length", &dsfp::Yin::frameLength)
        .def(
            "detect",
            [](dsfp::Yin& yin, const Array& frame) {
                if (frame.ndim() != 1 || frame.shape(0) < yin.frameLength())
                    throw py::value_error("frame must be 1-D with at least frame_length samples");
                dsfp::PitchEstimate e;
                {
                    py::gil_scoped_release release;
                    e = yin.detect(frame.data());
                }
                return std::make_tuple(e.f0, e.aperiodicity, e.voiced);
            },
            py::arg("frame"), "Returns (f0, aperiodicity, voiced) for frame[:frame_length].");

    py::class_<dsfp::FrequencyShifter>(m, "FrequencyShifter")
        .def(py::init<int, int>(), py::arg("n_fft") = 2048, py::arg("overlap") = 4)
        .def_property_readonly("latency", &dsfp::FrequencyShifter::latency)
        .def("reset", &dsfp::FrequencyShifter::reset)
        .def(
            "process",
            [](dsfp::FrequencyShifter& s, const Array& block, double ratio) {
                const auto n = static_cast<int>(block.size());
                Array out(n);
                const double* in = block.data();
                double* o = out.mutable_data();
                {
                    py::gil_scoped_release release;
                    s.process(in, o, n, ratio);
                }
                return out;
            },
            py::arg("block"), py::arg("ratio") = 1.0,
            "Shift a block by ratio; returns len(block) samples delayed by latency.");
}
