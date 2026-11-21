cmake_minimum_required(VERSION 3.22)

project(DSFPTune VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "Minimum macOS version")
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(DSFP_BUILD_PLUGIN "Build the AU / VST3 / Standalone plugin (needs JUCE)" ON)

# ---------------------------------------------------------------------------
# Core DSP: plain C++17, no dependencies. Port of the Python package (see README).
# ---------------------------------------------------------------------------
add_library(dsfp_dsp STATIC
    src/dsp/Fft.cpp
    src/dsp/Yin.cpp
    src/dsp/FrequencyShifter.cpp
    src/dsp/PitchCorrector.cpp)
target_include_directories(dsfp_dsp PUBLIC src)
set_target_properties(dsfp_dsp PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Command-line tool used by tests/test_cpp_port.py to compare against the Python version.
add_executable(dsfp_offline src/tools/dsfp_offline.cpp)
target_link_libraries(dsfp_offline PRIVATE dsfp_dsp)

if(NOT DSFP_BUILD_PLUGIN)
    return()
endif()

# ---------------------------------------------------------------------------
# JUCE: use a local checkout if given (-DDSFP_JUCE_DIR=...), else cpp/build/JUCE, else download.
# ---------------------------------------------------------------------------
set(DSFP_JUCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/build/JUCE" CACHE PATH "JUCE source checkout")
if(EXISTS "${DSFP_JUCE_DIR}/CMakeLists.txt")
    add_subdirectory("${DSFP_JUCE_DIR}" "${CMAKE_BINARY_DIR}/juce" EXCLUDE_FROM_ALL)
else()
    include(FetchContent)
    FetchContent_Declare(juce
        GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
        GIT_TAG 8.0.4
        GIT_SHALLOW TRUE)
    FetchContent_MakeAvailable(juce)
endif()

juce_add_plugin(DSFPTune
    PRODUCT_NAME "DSFP Tune"
    COMPANY_NAME "DSFP"
    BUNDLE_ID "com.dsfp.tune"
    PLUGIN_MANUFACTURER_CODE Dsfp
    PLUGIN_CODE Dstn
    FORMATS AU VST3 Standalone
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    AU_MAIN_TYPE kAudioUnitType_Effect
    VST3_CATEGORIES Fx Pitch
    MICROPHONE_PERMISSION_ENABLED TRUE
    MICROPHONE_PERMISSION_TEXT "DSFP Tune uses the microphone to pitch-correct your voice."
    COPY_PLUGIN_AFTER_BUILD TRUE)

target_sources(DSFPTune PRIVATE
    src/plugin/PluginProcessor.cpp
    src/plugin/PluginEditor.cpp)
target_compile_definitions(DSFPTune PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
    JUCE_DISPLAY_SPLASH_SCREEN=0)
target_link_libraries(DSFPTune
    PRIVATE dsfp_dsp juce::juce_audio_utils
    PUBLIC juce::juce_recommended_config_flags juce::juce_recommended_lto_flags juce::juce_recommended_warning_flags)

# Test host: loads the built AU / VST3 binary and runs audio through it (tests/test_cpp_port.py).
juce_add_console_app(dsfp_plugin_host PRODUCT_NAME "dsfp_plugin_host")
target_sources(dsfp_plugin_host PRIVATE src/tools/plugin_host.cpp)
target_compile_definitions(dsfp_plugin_host PRIVATE
    JUCE_PLUGINHOST_VST3=1
    JUCE_PLUGINHOST_AU=1
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0)
target_link_libraries(dsfp_plugin_host
    PRIVATE juce::juce_audio_processors juce::juce_audio_formats
    PUBLIC juce::juce_recommended_config_flags juce::juce_recommended_warning_flags)
if(APPLE)
    # Hosting Audio Units needs CoreAudioKit (AUGenericView), which JUCE doesn't link for console apps.
    target_link_libraries(dsfp_plugin_host PRIVATE "-framework CoreAudioKit")
endif()
