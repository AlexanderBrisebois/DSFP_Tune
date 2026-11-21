#include "plugin/PluginProcessor.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

#include "plugin/PluginEditor.h"

namespace {

juce::AudioParameterFloatAttributes withUnit(const juce::String& unit, int decimals = 0) {
    return juce::AudioParameterFloatAttributes()
        .withLabel(unit)
        .withStringFromValueFunction([unit, decimals](float v, int) { return juce::String(v, decimals) + " " + unit; })
        .withValueFromStringFunction([](const juce::String& text) { return text.retainCharacters("-0123456789.").getFloatValue(); });
}

std::unique_ptr<juce::AudioParameterFloat> floatParam(const char* id, const char* name, float lo, float hi, float step,
                                                      float def, const juce::String& unit) {
    return std::make_unique<juce::AudioParameterFloat>(juce::ParameterID{id, 1}, name,
                                                       juce::NormalisableRange<float>(lo, hi, step), def, withUnit(unit));
}

}  // namespace

const std::vector<int>& DSFPTuneProcessor::scaleChoices() {
    static const std::vector<int> choices = [] {
        std::vector<int> out;
        const auto& all = dsfp::scales();
        for (size_t i = 0; i < all.size(); ++i)
            if (std::strcmp(all[i].id, "chromatic") != 0) out.push_back(static_cast<int>(i));
        return out;
    }();
    return choices;
}

juce::StringArray DSFPTuneProcessor::tonicNames() {
    return {"C", "C#/Db", "D", "D#/Eb", "E", "F", "F#/Gb", "G", "G#/Ab", "A", "A#/Bb", "B"};
}

juce::AudioProcessorValueTreeState::ParameterLayout DSFPTuneProcessor::createLayout() {
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add(std::make_unique<juce::AudioParameterChoice>(
        juce::ParameterID{"mode", 1}, "Snap to", juce::StringArray{"Nearest note", "Nearest note in key"}, 0));
    layout.add(std::make_unique<juce::AudioParameterChoice>(juce::ParameterID{"tonic", 1}, "Key", tonicNames(), 0));

    juce::StringArray scaleNames;
    int majorIndex = 0;
    const auto& all = dsfp::scales();
    for (int idx : scaleChoices()) {
        if (std::strcmp(all[static_cast<size_t>(idx)].id, "major") == 0) majorIndex = scaleNames.size();
        scaleNames.add(all[static_cast<size_t>(idx)].label);
    }
    layout.add(std::make_unique<juce::AudioParameterChoice>(juce::ParameterID{"scale", 1}, "Scale", scaleNames, majorIndex));

    layout.add(floatParam("attack", "Attack", 0.0f, 200.0f, 1.0f, 25.0f, "ms"));
    layout.add(floatParam("release", "Release", 0.0f, 500.0f, 1.0f, 25.0f, "ms"));
    layout.add(floatParam("amount", "Amount", 0.0f, 100.0f, 1.0f, 100.0f, "%"));
    layout.add(floatParam("stickiness", "Stickiness", 0.0f, 50.0f, 1.0f, 15.0f, "ct"));
    layout.add(floatParam("noteWindow", "Note window", 0.0f, 400.0f, 1.0f, 120.0f, "ms"));
    layout.add(floatParam("noteJump", "Note jump", 25.0f, 200.0f, 1.0f, 75.0f, "ct"));
    layout.add(floatParam("hold", "Hold", 0.0f, 300.0f, 1.0f, 60.0f, "ms"));
    layout.add(floatParam("maxCorrection", "Max correction", 50.0f, 1200.0f, 10.0f, 200.0f, "ct"));
    return layout;
}

DSFPTuneProcessor::DSFPTuneProcessor()
    : AudioProcessor(BusesProperties()
                         .withInput("Input", juce::AudioChannelSet::stereo(), true)
                         .withOutput("Output", juce::AudioChannelSet::stereo(), true)),
      apvts_(*this, nullptr, "DSFPTune", createLayout()) {
    mode_ = apvts_.getRawParameterValue("mode");
    tonic_ = apvts_.getRawParameterValue("tonic");
    scale_ = apvts_.getRawParameterValue("scale");
    attack_ = apvts_.getRawParameterValue("attack");
    release_ = apvts_.getRawParameterValue("release");
    amount_ = apvts_.getRawParameterValue("amount");
    stickiness_ = apvts_.getRawParameterValue("stickiness");
    noteWindow_ = apvts_.getRawParameterValue("noteWindow");
    noteJump_ = apvts_.getRawParameterValue("noteJump");
    hold_ = apvts_.getRawParameterValue("hold");
    maxCorrection_ = apvts_.getRawParameterValue("maxCorrection");
    displayTargetMidi.store(std::numeric_limits<float>::quiet_NaN());
}

dsfp::CorrectorParams DSFPTuneProcessor::currentParams() const {
    dsfp::CorrectorParams p;
    p.retuneMs = static_cast<double>(attack_->load());
    p.releaseMs = static_cast<double>(release_->load());
    p.amount = static_cast<double>(amount_->load()) / 100.0;
    p.hysteresis = static_cast<double>(stickiness_->load()) / 100.0;
    p.noteWindowMs = static_cast<double>(noteWindow_->load());
    p.noteJump = static_cast<double>(noteJump_->load()) / 100.0;
    p.holdMs = static_cast<double>(hold_->load());
    p.maxCorrection = static_cast<double>(maxCorrection_->load()) / 100.0;

    const int mode = static_cast<int>(std::lround(mode_->load()));
    if (mode == 0) {
        p.allowed = dsfp::chromaticPitchClasses();
    } else {
        const auto& choices = scaleChoices();
        const int choice = std::clamp(static_cast<int>(std::lround(scale_->load())), 0, static_cast<int>(choices.size()) - 1);
        const int tonic = std::clamp(static_cast<int>(std::lround(tonic_->load())), 0, 11);
        p.allowed = dsfp::keyPitchClasses(tonic, choices[static_cast<size_t>(choice)]);
    }
    return p;
}

void DSFPTuneProcessor::prepareToPlay(double sampleRate, int samplesPerBlock) {
    const int nFft = sampleRate <= 50000.0 ? 1024 : (sampleRate <= 100000.0 ? 2048 : 4096);
    const double fMax = std::min(1000.0, sampleRate / 4.0);
    corrector_ = std::make_unique<dsfp::PitchCorrector>(sampleRate, nFft, 4, 256, 70.0, fMax);
    corrector_->setParams(currentParams());
    setLatencySamples(corrector_->latency());
    mono_.assign(static_cast<size_t>(std::max(samplesPerBlock, 4096)), 0.0);
    displayVoiced.store(false);
    displayTargetMidi.store(std::numeric_limits<float>::quiet_NaN());
    displayCorrection.store(0.0f);
}

void DSFPTuneProcessor::releaseResources() {
    if (corrector_) corrector_->reset();
}

void DSFPTuneProcessor::reset() {
    if (corrector_) corrector_->reset();
}

bool DSFPTuneProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const {
    const auto in = layouts.getMainInputChannelSet();
    const auto out = layouts.getMainOutputChannelSet();
    if (in != out) return false;
    return out == juce::AudioChannelSet::mono() || out == juce::AudioChannelSet::stereo();
}

void DSFPTuneProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi) {
    juce::ignoreUnused(midi);
    juce::ScopedNoDenormals noDenormals;

    const int numIn = getTotalNumInputChannels();
    const int numOut = getTotalNumOutputChannels();
    const int numSamples = buffer.getNumSamples();
    for (int ch = numIn; ch < numOut; ++ch) buffer.clear(ch, 0, numSamples);

    if (corrector_ == nullptr || numIn == 0 || mono_.empty()) return;
    corrector_->setParams(currentParams());

    const int chunkMax = static_cast<int>(mono_.size());
    const double inScale = 1.0 / numIn;
    for (int start = 0; start < numSamples; start += chunkMax) {
        const int n = std::min(chunkMax, numSamples - start);
        // Mono sum of the inputs.
        for (int i = 0; i < n; ++i) {
            double s = 0.0;
            for (int ch = 0; ch < numIn; ++ch) s += static_cast<double>(buffer.getReadPointer(ch)[start + i]);
            mono_[static_cast<size_t>(i)] = s * inScale;
        }
        corrector_->process(mono_.data(), mono_.data(), n);
        for (int ch = 0; ch < numOut; ++ch) {
            float* dst = buffer.getWritePointer(ch) + start;
            for (int i = 0; i < n; ++i) dst[i] = static_cast<float>(mono_[static_cast<size_t>(i)]);
        }
    }

    const auto& st = corrector_->status();
    displayF0.store(static_cast<float>(st.f0));
    displayVoiced.store(st.voiced);
    displayTargetMidi.store(static_cast<float>(st.targetMidi));
    displayCorrection.store(static_cast<float>(st.correction));
}

juce::AudioProcessorEditor* DSFPTuneProcessor::createEditor() { return new DSFPTuneEditor(*this); }

void DSFPTuneProcessor::getStateInformation(juce::MemoryBlock& destData) {
    auto st = apvts_.copyState();
    if (auto xml = st.createXml()) copyXmlToBinary(*xml, destData);
}

void DSFPTuneProcessor::setStateInformation(const void* data, int sizeInBytes) {
    if (auto xml = getXmlFromBinary(data, sizeInBytes))
        if (xml->hasTagName(apvts_.state.getType())) apvts_.replaceState(juce::ValueTree::fromXml(*xml));
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() { return new DSFPTuneProcessor(); }
