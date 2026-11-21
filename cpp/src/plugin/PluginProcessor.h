// DSFP Tune audio plugin (AU / VST3 / Standalone): wraps dsfp::PitchCorrector.
#pragma once

#include <atomic>
#include <memory>
#include <vector>

#include <juce_audio_processors/juce_audio_processors.h>

#include "dsp/PitchCorrector.h"

class DSFPTuneProcessor : public juce::AudioProcessor {
public:
    DSFPTuneProcessor();
    ~DSFPTuneProcessor() override = default;

    // Indices of dsfp::scales() offered by the "scale" parameter (chromatic is a separate mode).
    static const std::vector<int>& scaleChoices();
    static juce::StringArray tonicNames();

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    void reset() override;
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;
    using AudioProcessor::processBlock;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "DSFP Tune"; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    bool isMidiEffect() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return {}; }
    void changeProgramName(int, const juce::String&) override {}

    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    juce::AudioProcessorValueTreeState& state() { return apvts_; }

    // Latest pitch estimate, published by the audio thread for the editor.
    std::atomic<float> displayF0{0.0f};
    std::atomic<bool> displayVoiced{false};
    std::atomic<float> displayTargetMidi{0.0f};  // NaN when not correcting
    std::atomic<float> displayCorrection{0.0f};  // semitones

private:
    static juce::AudioProcessorValueTreeState::ParameterLayout createLayout();
    dsfp::CorrectorParams currentParams() const;

    juce::AudioProcessorValueTreeState apvts_;
    std::atomic<float>* mode_ = nullptr;
    std::atomic<float>* tonic_ = nullptr;
    std::atomic<float>* scale_ = nullptr;
    std::atomic<float>* attack_ = nullptr;
    std::atomic<float>* release_ = nullptr;
    std::atomic<float>* amount_ = nullptr;
    std::atomic<float>* stickiness_ = nullptr;
    std::atomic<float>* noteWindow_ = nullptr;
    std::atomic<float>* noteJump_ = nullptr;
    std::atomic<float>* hold_ = nullptr;
    std::atomic<float>* maxCorrection_ = nullptr;

    std::unique_ptr<dsfp::PitchCorrector> corrector_;
    std::vector<double> mono_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(DSFPTuneProcessor)
};
