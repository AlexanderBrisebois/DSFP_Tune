#include "plugin/PluginEditor.h"

#include <cmath>

namespace {

const juce::Colour kBg(0xff0f1115);
const juce::Colour kPanel(0xff191d24);
const juce::Colour kPanel2(0xff20252e);
const juce::Colour kBorder(0xff2c323c);
const juce::Colour kText(0xfff3f5f8);
const juce::Colour kMuted(0xff9aa3b0);
const juce::Colour kAccent(0xff2dd4bf);
const juce::Colour kOffKey(0xfff5a524);

const char* const kNoteNames[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

juce::String noteName(int midi) {
    const int pc = ((midi % 12) + 12) % 12;
    const int octave = static_cast<int>(std::floor(midi / 12.0)) - 1;
    return juce::String(kNoteNames[pc]) + juce::String(octave);
}

juce::String centsText(double cents) {
    const int c = static_cast<int>(std::lround(cents));
    return (c >= 0 ? "+" : juce::String::fromUTF8("\xe2\x88\x92")) + juce::String(std::abs(c)) + " ct";
}

}  // namespace

// ---------------------------------------------------------------------------------------------
DSFPTuneLookAndFeel::DSFPTuneLookAndFeel() {
    setColour(juce::ResizableWindow::backgroundColourId, kBg);
    setColour(juce::Label::textColourId, kText);
    setColour(juce::Slider::textBoxTextColourId, kText);
    setColour(juce::Slider::textBoxOutlineColourId, juce::Colours::transparentBlack);
    setColour(juce::Slider::textBoxBackgroundColourId, juce::Colours::transparentBlack);
    setColour(juce::Slider::rotarySliderFillColourId, kAccent);
    setColour(juce::Slider::rotarySliderOutlineColourId, kBorder);
    setColour(juce::Slider::thumbColourId, kText);
    setColour(juce::ComboBox::backgroundColourId, kPanel2);
    setColour(juce::ComboBox::outlineColourId, kBorder);
    setColour(juce::ComboBox::textColourId, kText);
    setColour(juce::ComboBox::arrowColourId, kMuted);
    setColour(juce::PopupMenu::backgroundColourId, kPanel2);
    setColour(juce::PopupMenu::textColourId, kText);
    setColour(juce::PopupMenu::highlightedBackgroundColourId, kAccent.withAlpha(0.25f));
    setColour(juce::PopupMenu::highlightedTextColourId, kText);
    setColour(juce::TooltipWindow::backgroundColourId, kPanel2);
    setColour(juce::TooltipWindow::textColourId, kText);
    setColour(juce::TooltipWindow::outlineColourId, kBorder);
}

void DSFPTuneLookAndFeel::drawRotarySlider(juce::Graphics& g, int x, int y, int width, int height, float sliderPos,
                                           float startAngle, float endAngle, juce::Slider& slider) {
    const auto bounds = juce::Rectangle<int>(x, y, width, height).toFloat().reduced(6.0f);
    const float radius = juce::jmin(bounds.getWidth(), bounds.getHeight()) / 2.0f;
    const auto centre = bounds.getCentre();
    const float lineW = juce::jmax(3.0f, radius * 0.12f);
    const float arcRadius = radius - lineW * 0.5f;
    const float angle = startAngle + sliderPos * (endAngle - startAngle);
    const bool enabled = slider.isEnabled();

    juce::Path track;
    track.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, startAngle, endAngle, true);
    g.setColour(kBorder);
    g.strokePath(track, juce::PathStrokeType(lineW, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    if (sliderPos > 0.0f) {
        juce::Path value;
        value.addCentredArc(centre.x, centre.y, arcRadius, arcRadius, 0.0f, startAngle, angle, true);
        g.setColour(enabled ? kAccent : kMuted);
        g.strokePath(value, juce::PathStrokeType(lineW, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
    }

    const float knobR = arcRadius - lineW * 1.4f;
    g.setColour(kPanel2);
    g.fillEllipse(centre.x - knobR, centre.y - knobR, knobR * 2.0f, knobR * 2.0f);
    g.setColour(kBorder);
    g.drawEllipse(centre.x - knobR, centre.y - knobR, knobR * 2.0f, knobR * 2.0f, 1.0f);

    const juce::Point<float> tip(centre.x + (knobR - 4.0f) * std::sin(angle), centre.y - (knobR - 4.0f) * std::cos(angle));
    const juce::Point<float> inner(centre.x + knobR * 0.35f * std::sin(angle), centre.y - knobR * 0.35f * std::cos(angle));
    g.setColour(kText);
    g.drawLine({inner, tip}, 2.2f);
}

void DSFPTuneLookAndFeel::drawComboBox(juce::Graphics& g, int width, int height, bool, int, int, int, int,
                                       juce::ComboBox& box) {
    const auto r = juce::Rectangle<int>(0, 0, width, height).toFloat().reduced(0.5f);
    g.setColour(box.findColour(juce::ComboBox::backgroundColourId));
    g.fillRoundedRectangle(r, 8.0f);
    g.setColour(box.hasKeyboardFocus(true) ? kAccent : box.findColour(juce::ComboBox::outlineColourId));
    g.drawRoundedRectangle(r, 8.0f, 1.0f);

    const float ax = static_cast<float>(width) - 18.0f, ay = static_cast<float>(height) * 0.5f;
    juce::Path arrow;
    arrow.startNewSubPath(ax - 4.0f, ay - 2.0f);
    arrow.lineTo(ax, ay + 2.5f);
    arrow.lineTo(ax + 4.0f, ay - 2.0f);
    g.setColour(box.findColour(juce::ComboBox::arrowColourId).withAlpha(box.isEnabled() ? 1.0f : 0.4f));
    g.strokePath(arrow, juce::PathStrokeType(1.6f, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
}

// ---------------------------------------------------------------------------------------------
DSFPTuneEditor::DSFPTuneEditor(DSFPTuneProcessor& p) : AudioProcessorEditor(&p), processor_(p) {
    setLookAndFeel(&lnf_);

    setupChoice(mode_, "mode", "Snap to");
    setupChoice(tonic_, "tonic", "Key");
    setupChoice(scale_, "scale", "Scale");

    setupKnob(knobs_[0], "attack", "Attack",
              "How fast the voice is pulled onto the note. 0 = instant and robotic; 100+ = gentle.");
    setupKnob(knobs_[1], "release", "Release", "How fast the correction lets go when a note ends or its pitch is lost.");
    setupKnob(knobs_[2], "amount", "Amount",
              "How far notes are pulled: 100% lands exactly on the note, 50% goes halfway.");
    setupKnob(knobs_[3], "maxCorrection", "Max correction",
              "Never shifts further than this, so pitch-detection errors are left alone.");
    setupKnob(knobs_[4], "stickiness", "Stickiness",
              "How far past the halfway point between two notes before the target switches.");
    setupKnob(knobs_[5], "noteWindow", "Note window",
              "The pitch is averaged over this long to choose the note, so vibrato doesn't flip notes.");
    setupKnob(knobs_[6], "noteJump", "Note jump", "A jump bigger than this switches to the new note right away.");
    setupKnob(knobs_[7], "hold", "Hold", "Keeps correcting through short gaps in pitch detection inside a note.");

    setSize(760, 470);
    setResizable(false, false);
    startTimerHz(30);
    timerCallback();
}

DSFPTuneEditor::~DSFPTuneEditor() {
    stopTimer();
    setLookAndFeel(nullptr);
}

void DSFPTuneEditor::setupChoice(Choice& c, const juce::String& paramId, const juce::String& title) {
    c.label.setText(title, juce::dontSendNotification);
    c.label.setColour(juce::Label::textColourId, kMuted);
    c.label.setFont(juce::FontOptions(12.0f, juce::Font::bold));
    addAndMakeVisible(c.label);
    if (auto* param = dynamic_cast<juce::AudioParameterChoice*>(processor_.state().getParameter(paramId)))
        c.box.addItemList(param->choices, 1);
    addAndMakeVisible(c.box);
    c.attachment = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment>(processor_.state(), paramId, c.box);
}

void DSFPTuneEditor::setupKnob(Knob& k, const juce::String& paramId, const juce::String& title, const juce::String& tooltip) {
    k.slider.setSliderStyle(juce::Slider::RotaryHorizontalVerticalDrag);
    k.slider.setTextBoxStyle(juce::Slider::TextBoxBelow, false, 90, 20);
    k.slider.setTooltip(tooltip);
    addAndMakeVisible(k.slider);
    k.label.setText(title, juce::dontSendNotification);
    k.label.setJustificationType(juce::Justification::centred);
    k.label.setColour(juce::Label::textColourId, kMuted);
    k.label.setFont(juce::FontOptions(12.5f, juce::Font::bold));
    k.label.setTooltip(tooltip);
    addAndMakeVisible(k.label);
    k.attachment = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment>(processor_.state(), paramId, k.slider);
}

void DSFPTuneEditor::timerCallback() {
    const bool keyMode = processor_.state().getRawParameterValue("mode")->load() > 0.5f;
    tonic_.box.setEnabled(keyMode);
    scale_.box.setEnabled(keyMode);
    tonic_.box.setAlpha(keyMode ? 1.0f : 0.45f);
    scale_.box.setAlpha(keyMode ? 1.0f : 0.45f);
    tonic_.label.setAlpha(keyMode ? 1.0f : 0.45f);
    scale_.label.setAlpha(keyMode ? 1.0f : 0.45f);

    const bool voiced = processor_.displayVoiced.load();
    const double f0 = static_cast<double>(processor_.displayF0.load());
    const double target = static_cast<double>(processor_.displayTargetMidi.load());
    const double corr = static_cast<double>(processor_.displayCorrection.load());

    juce::String det = "–", sub = "no pitch", tgt = "–", shift;
    bool inKey = true;
    if (voiced && std::isfinite(f0) && f0 > 0.0) {
        const double midi = 69.0 + 12.0 * std::log2(f0 / 440.0);
        const int nearest = static_cast<int>(std::lround(midi));
        det = noteName(nearest) + "  " + centsText(100.0 * (midi - nearest));
        sub = juce::String(f0, 1) + " Hz";
        if (std::isfinite(target)) {
            const int t = static_cast<int>(std::lround(target));
            tgt = noteName(t);
            shift = "shift " + centsText(100.0 * corr);
            inKey = std::abs(100.0 * (midi - nearest)) <= 10.0;
        } else {
            shift = "not correcting";
        }
    }
    if (det != detectedText_ || sub != detectedSub_ || tgt != targetText_ || shift != shiftText_ || inKey != inKeyColour_) {
        detectedText_ = det;
        detectedSub_ = sub;
        targetText_ = tgt;
        shiftText_ = shift;
        inKeyColour_ = inKey;
        repaint(readoutArea_);
    }
}

void DSFPTuneEditor::paint(juce::Graphics& g) {
    g.fillAll(kBg);

    // Header.
    auto header = getLocalBounds().removeFromTop(86).reduced(20, 14);
    const auto mark = header.removeFromLeft(40).withSizeKeepingCentre(36, 36).toFloat();
    g.setColour(kPanel2);
    g.fillRoundedRectangle(mark, 9.0f);
    juce::Path wave;
    wave.startNewSubPath(mark.getX() + 5.0f, mark.getCentreY() + 2.0f);
    for (int i = 1; i <= 24; ++i) {
        const float t = static_cast<float>(i) / 24.0f;
        wave.lineTo(mark.getX() + 5.0f + t * (mark.getWidth() - 10.0f),
                    mark.getCentreY() + 2.0f - 9.0f * std::sin(t * juce::MathConstants<float>::twoPi * 1.5f) * (0.4f + 0.6f * t));
    }
    g.setColour(kAccent);
    g.strokePath(wave, juce::PathStrokeType(2.4f, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
    header.removeFromLeft(12);
    auto titleArea = header.removeFromLeft(200);
    g.setColour(kText);
    g.setFont(juce::FontOptions(22.0f, juce::Font::bold));
    g.drawText("DSFP Tune", titleArea.removeFromTop(titleArea.getHeight() / 2 + 4), juce::Justification::bottomLeft);
    g.setColour(kMuted);
    g.setFont(juce::FontOptions(13.0f));
    g.drawText("pitch correction", titleArea, juce::Justification::topLeft);

    // Readout panel.
    g.setColour(kPanel);
    g.fillRoundedRectangle(readoutArea_.toFloat(), 12.0f);
    g.setColour(kBorder);
    g.drawRoundedRectangle(readoutArea_.toFloat().reduced(0.5f), 12.0f, 1.0f);
    auto ro = readoutArea_.reduced(16, 8);
    auto left = ro.removeFromLeft(ro.getWidth() / 2);
    g.setColour(kMuted);
    g.setFont(juce::FontOptions(11.0f, juce::Font::bold));
    g.drawText("DETECTED", left.removeFromTop(16), juce::Justification::centredLeft);
    g.setColour(detectedSub_ == "no pitch" ? kMuted : (inKeyColour_ ? kAccent : kOffKey));
    g.setFont(juce::FontOptions(20.0f, juce::Font::bold));
    g.drawText(detectedText_, left.removeFromTop(24), juce::Justification::centredLeft);
    g.setColour(kMuted);
    g.setFont(juce::FontOptions(12.0f));
    g.drawText(detectedSub_, left, juce::Justification::topLeft);

    g.setColour(kMuted);
    g.setFont(juce::FontOptions(11.0f, juce::Font::bold));
    g.drawText(juce::String::fromUTF8("\xe2\x86\x92 TARGET"), ro.removeFromTop(16), juce::Justification::centredLeft);
    g.setColour(kText);
    g.setFont(juce::FontOptions(20.0f, juce::Font::bold));
    g.drawText(targetText_, ro.removeFromTop(24), juce::Justification::centredLeft);
    g.setColour(kMuted);
    g.setFont(juce::FontOptions(12.0f));
    g.drawText(shiftText_, ro, juce::Justification::topLeft);

    // Control panels.
    const auto panel = [&g](juce::Rectangle<int> r) {
        g.setColour(kPanel);
        g.fillRoundedRectangle(r.toFloat(), 12.0f);
        g.setColour(kBorder);
        g.drawRoundedRectangle(r.toFloat().reduced(0.5f), 12.0f, 1.0f);
    };
    auto body = getLocalBounds().withTrimmedTop(86).reduced(20, 0).withTrimmedBottom(20);
    panel(body.removeFromTop(78));
    body.removeFromTop(14);
    panel(body);
}

void DSFPTuneEditor::resized() {
    auto header = getLocalBounds().removeFromTop(86).reduced(20, 14);
    readoutArea_ = header.removeFromRight(330);

    auto body = getLocalBounds().withTrimmedTop(86).reduced(20, 0).withTrimmedBottom(20);
    auto choices = body.removeFromTop(78).reduced(16, 12);
    const int gap = 14;
    const int w = (choices.getWidth() - 2 * gap) / 3;
    for (auto* c : {&mode_, &tonic_, &scale_}) {
        auto col = choices.removeFromLeft(w);
        choices.removeFromLeft(gap);
        c->label.setBounds(col.removeFromTop(18));
        col.removeFromTop(4);
        c->box.setBounds(col.removeFromTop(30));
    }

    body.removeFromTop(14);
    auto knobs = body.reduced(12, 10);
    const int rowH = knobs.getHeight() / 2;
    for (int row = 0; row < 2; ++row) {
        auto r = knobs.removeFromTop(rowH);
        const int kw = r.getWidth() / 4;
        for (int i = 0; i < 4; ++i) {
            auto cell = r.removeFromLeft(kw).reduced(6, 2);
            auto& k = knobs_[static_cast<size_t>(row * 4 + i)];
            k.label.setBounds(cell.removeFromTop(18));
            k.slider.setBounds(cell);
        }
    }
}
