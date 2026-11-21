// DSFP Tune plugin window: snap settings, correction knobs and a live note readout.
#pragma once

#include <array>
#include <memory>

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_basics/juce_gui_basics.h>

#include "plugin/PluginProcessor.h"

class DSFPTuneLookAndFeel : public juce::LookAndFeel_V4 {
public:
    DSFPTuneLookAndFeel();
    void drawRotarySlider(juce::Graphics&, int x, int y, int width, int height, float sliderPos, float startAngle,
                          float endAngle, juce::Slider&) override;
    void drawComboBox(juce::Graphics&, int width, int height, bool isButtonDown, int buttonX, int buttonY, int buttonW,
                      int buttonH, juce::ComboBox&) override;
};

class DSFPTuneEditor : public juce::AudioProcessorEditor, private juce::Timer {
public:
    explicit DSFPTuneEditor(DSFPTuneProcessor&);
    ~DSFPTuneEditor() override;

    void paint(juce::Graphics&) override;
    void resized() override;

private:
    struct Knob {
        juce::Slider slider;
        juce::Label label;
        std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> attachment;
    };
    struct Choice {
        juce::ComboBox box;
        juce::Label label;
        std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> attachment;
    };

    void timerCallback() override;
    void setupKnob(Knob&, const juce::String& paramId, const juce::String& title, const juce::String& tooltip);
    void setupChoice(Choice&, const juce::String& paramId, const juce::String& title);

    DSFPTuneProcessor& processor_;
    DSFPTuneLookAndFeel lnf_;
    juce::TooltipWindow tooltips_{this, 600};

    Choice mode_, tonic_, scale_;
    std::array<Knob, 8> knobs_;

    juce::String detectedText_ = "–", detectedSub_ = "no pitch", targetText_ = "–", shiftText_;
    bool inKeyColour_ = true;
    juce::Rectangle<int> readoutArea_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(DSFPTuneEditor)
};
