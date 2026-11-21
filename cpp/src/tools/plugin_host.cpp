// dsfp_plugin_host: load a built plugin (.vst3 or .component) and render a mono file through it.
//
//   dsfp_plugin_host <plugin path> <input.f32> <output.f32> <sampleRate> [blockSize=512] [param=value ...]
//
// Input and output are raw little-endian float32 mono. The output is aligned with the input
// (the plugin's reported latency is removed) and has the same length. Parameters are matched by
// ID or display name, ignoring case, spaces and punctuation ("noteWindow", "Note window" and
// "notewindow" all match). Hosted AU/VST3 plugins expose numeric IDs, so use display names for
// parameters whose ID differs from the name, e.g. "Snap to=Nearest note in key" or "Key=A".
// Values are parsed by the plugin, e.g. "amount=50".
#include <cstdio>
#include <fstream>
#include <iostream>
#include <vector>

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_events/juce_events.h>

namespace {

bool readF32(const juce::String& path, std::vector<float>& out) {
    std::ifstream f(path.toStdString(), std::ios::binary | std::ios::ate);
    if (!f) return false;
    const auto bytes = static_cast<size_t>(f.tellg());
    out.resize(bytes / sizeof(float));
    f.seekg(0);
    f.read(reinterpret_cast<char*>(out.data()), static_cast<std::streamsize>(out.size() * sizeof(float)));
    return static_cast<bool>(f);
}

bool writeF32(const juce::String& path, const std::vector<float>& data) {
    std::ofstream f(path.toStdString(), std::ios::binary);
    f.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size() * sizeof(float)));
    return static_cast<bool>(f);
}

juce::String normalised(const juce::String& s) {
    return s.toLowerCase().retainCharacters("abcdefghijklmnopqrstuvwxyz0123456789");
}

juce::String paramIdOf(juce::AudioProcessorParameter* p) {
    if (auto* withId = dynamic_cast<juce::AudioProcessorParameterWithID*>(p)) return withId->paramID;
    if (auto* hosted = dynamic_cast<juce::HostedAudioProcessorParameter*>(p)) return hosted->getParameterID();
    return {};
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 5) {
        std::cerr << "usage: dsfp_plugin_host <plugin> <input.f32> <output.f32> <sampleRate> [blockSize=512] [param=value ...]\n";
        return 2;
    }
    const juce::String pluginPath = juce::File::getCurrentWorkingDirectory().getChildFile(argv[1]).getFullPathName();
    const juce::String inPath(argv[2]), outPath(argv[3]);
    const double sampleRate = juce::String(argv[4]).getDoubleValue();
    int argi = 5;
    int blockSize = 512;
    if (argc > argi && !juce::String(argv[argi]).containsChar('=')) blockSize = juce::String(argv[argi++]).getIntValue();
    if (sampleRate <= 0 || blockSize <= 0) {
        std::cerr << "bad sample rate or block size\n";
        return 2;
    }

    juce::ScopedJuceInitialiser_GUI juceInit;

    std::vector<float> input;
    if (!readF32(inPath, input)) {
        std::cerr << "cannot read " << inPath << "\n";
        return 1;
    }

    juce::AudioPluginFormatManager formats;
    formats.addDefaultFormats();

    juce::OwnedArray<juce::PluginDescription> types;
    for (auto* format : formats.getFormats()) {
        if (!format->fileMightContainThisPluginType(pluginPath)) continue;
        format->findAllTypesForFile(types, pluginPath);
        if (!types.isEmpty()) break;
    }
    if (types.isEmpty()) {
        std::cerr << "no plugin found in " << pluginPath << "\n";
        return 1;
    }

    juce::String error;
    auto instance = formats.createPluginInstance(*types[0], sampleRate, blockSize, error);
    if (instance == nullptr) {
        std::cerr << "could not load plugin: " << error << "\n";
        return 1;
    }
    std::cout << "plugin: " << instance->getName() << " (" << types[0]->pluginFormatName << ")\n";

    // Parameters.
    for (; argi < argc; ++argi) {
        const juce::String arg(argv[argi]);
        const auto name = normalised(arg.upToFirstOccurrenceOf("=", false, false));
        const auto value = arg.fromFirstOccurrenceOf("=", false, false).trim();
        bool applied = false;
        for (auto* p : instance->getParameters()) {
            if (normalised(paramIdOf(p)) == name || normalised(p->getName(64)) == name) {
                p->setValueNotifyingHost(p->getValueForText(value));
                std::cout << "param " << p->getName(64) << " = " << p->getText(p->getValue(), 64) << "\n";
                applied = true;
                break;
            }
        }
        if (!applied) {
            std::cerr << "unknown parameter " << arg.upToFirstOccurrenceOf("=", false, false) << "; available:";
            for (auto* p : instance->getParameters()) std::cerr << " \"" << p->getName(64) << "\"";
            std::cerr << "\n";
            return 1;
        }
    }

    // Bus layout: mono if accepted, else stereo with the signal on both inputs.
    juce::AudioProcessor::BusesLayout mono;
    mono.inputBuses.add(juce::AudioChannelSet::mono());
    mono.outputBuses.add(juce::AudioChannelSet::mono());
    if (!instance->setBusesLayout(mono)) {
        juce::AudioProcessor::BusesLayout stereo;
        stereo.inputBuses.add(juce::AudioChannelSet::stereo());
        stereo.outputBuses.add(juce::AudioChannelSet::stereo());
        instance->setBusesLayout(stereo);
    }
    const int numIn = instance->getTotalNumInputChannels();
    const int numOut = instance->getTotalNumOutputChannels();
    std::cout << "channels: " << numIn << " in, " << numOut << " out\n";

    instance->setRateAndBufferSizeDetails(sampleRate, blockSize);
    instance->prepareToPlay(sampleRate, blockSize);
    const int latency = instance->getLatencySamples();
    std::cout << "latency: " << latency << " samples\n";

    const size_t total = input.size() + static_cast<size_t>(latency);
    std::vector<float> rendered;
    rendered.reserve(total);
    juce::AudioBuffer<float> buffer(juce::jmax(numIn, numOut, 1), blockSize);
    juce::MidiBuffer midi;
    int blocks = 0;
    for (size_t pos = 0; pos < total; pos += static_cast<size_t>(blockSize)) {
        const int n = static_cast<int>(std::min(static_cast<size_t>(blockSize), total - pos));
        buffer.setSize(buffer.getNumChannels(), n, false, false, true);
        buffer.clear();
        for (int ch = 0; ch < numIn; ++ch) {
            float* dst = buffer.getWritePointer(ch);
            for (int i = 0; i < n; ++i) {
                const size_t idx = pos + static_cast<size_t>(i);
                dst[i] = idx < input.size() ? input[idx] : 0.0f;
            }
        }
        instance->processBlock(buffer, midi);
        const float* out = buffer.getReadPointer(0);
        rendered.insert(rendered.end(), out, out + n);
        ++blocks;
    }
    instance->releaseResources();

    std::vector<float> aligned(rendered.begin() + latency, rendered.begin() + latency + static_cast<long>(input.size()));
    if (!writeF32(outPath, aligned)) {
        std::cerr << "cannot write " << outPath << "\n";
        return 1;
    }
    std::cout << "processed " << blocks << " blocks of " << blockSize << " (" << input.size() << " samples)\n";
    return 0;
}
