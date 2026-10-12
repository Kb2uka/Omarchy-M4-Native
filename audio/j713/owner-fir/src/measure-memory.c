// SPDX-License-Identifier: MIT
// Copyright (c) 2026 KB2UKA
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHANNELS 4
#define BLOCK 256
#define FRAMES 73728
#define IMPULSE 4096

struct input {
    unsigned lane;
    float amplitude;
    unsigned calls;
    unsigned impulses;
    const float *file_samples;
    float samples[CHANNELS][BLOCK];
};

static OSStatus supply(void *ref, AudioUnitRenderActionFlags *flags,
                       const AudioTimeStamp *stamp, UInt32 bus,
                       UInt32 frames, AudioBufferList *buffers) {
    (void)flags;
    struct input *input = ref;
    if (bus != 0 || frames > BLOCK || buffers->mNumberBuffers != CHANNELS ||
        !(stamp->mFlags & kAudioTimeStampSampleTimeValid) ||
        !isfinite(stamp->mSampleTime) || stamp->mSampleTime != floor(stamp->mSampleTime))
        return kAudio_ParamError;
    input->calls++;
    for (unsigned ch = 0; ch < CHANNELS; ch++) {
        if (buffers->mBuffers[ch].mNumberChannels != 1) return kAudio_ParamError;
        buffers->mBuffers[ch].mData = input->samples[ch];
        buffers->mBuffers[ch].mDataByteSize = frames * sizeof(float);
        memset(input->samples[ch], 0, frames * sizeof(float));
    }
    if (input->file_samples) {
        long start = (long)stamp->mSampleTime;
        if (start < 0 || start + frames > FRAMES) return kAudio_ParamError;
        for (unsigned ch = 0; ch < CHANNELS; ch++)
            memcpy(input->samples[ch], input->file_samples + ch * FRAMES + start, frames * sizeof(float));
        return noErr;
    }
    long offset = IMPULSE - (long)stamp->mSampleTime;
    if (offset >= 0 && offset < frames) {
        input->samples[input->lane][offset] = input->amplitude;
        input->impulses++;
    }
    return noErr;
}

static int fail(const char *label, OSStatus status) {
    fprintf(stderr, "%s status=%d\n", label, (int)status);
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 8) return fail("usage: preset output lane amplitude bypass input-or-impulse subset", -1);
    char *end;
    unsigned lane = (unsigned)strtoul(argv[3], &end, 10);
    if (*end || lane >= CHANNELS) return 2;
    float amplitude = strtof(argv[4], &end);
    if (*end || !isfinite(amplitude) || (amplitude != 0.0001f && amplitude != 0.0002f)) return 3;
    unsigned bypass = (unsigned)strtoul(argv[5], &end, 10);
    if (*end || bypass > 1) return 4;
    void *handle = dlopen("/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP", RTLD_NOW | RTLD_LOCAL);
    if (!handle) return fail("dlopen", -1);
    void (*reg)(void) = dlsym(handle, "RegisterAudioUnits_Internal");
    if (!reg) return fail("registration", -1);
    reg();
    AudioComponentDescription d = { .componentType = 'aufx', .componentSubType = 'tmst', .componentManufacturer = 'appl' };
    AudioComponent c = AudioComponentFindNext(NULL, &d);
    AudioComponentInstance au = NULL;
    if (!c) return fail("component", -1);
    OSStatus status = AudioComponentInstanceNew(c, &au);
    if (status || !au) return fail("instance", status);
    FILE *file = fopen(argv[1], "rb");
    if (!file) return fail("preset open", -1);
    if (fseek(file, 0, SEEK_END)) return 5;
    long count = ftell(file);
    if (count <= 0 || count > 1000000 || fseek(file, 0, SEEK_SET)) return 6;
    unsigned char *raw = malloc((size_t)count);
    if (!raw || fread(raw, 1, (size_t)count, file) != (size_t)count) return 7;
    fclose(file);
    CFDataRef data = CFDataCreate(NULL, raw, count);
    free(raw);
    CFPropertyListRef preset = CFPropertyListCreateWithData(NULL, data, kCFPropertyListImmutable, NULL, NULL);
    CFRelease(data);
    if (!preset || CFGetTypeID(preset) != CFDictionaryGetTypeID()) return 8;
    status = AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &preset, sizeof(preset));
    CFRelease(preset);
    if (status) return fail("preset load", status);
    int subset = 0;
    if (!strcmp(argv[7], "hp90")) subset = 1;
    else if (!strcmp(argv[7], "hp80")) subset = 2;
    else if (strcmp(argv[7], "none")) return fail("unknown subset", -1);
    if (subset) {
        unsigned retained = subset == 1 ? 0 : 5;
        AudioUnitParameterValue type, frequency, order;
        if (AudioUnitGetParameter(au, 15 + retained * 10, 0, 0, &type) || type != 4 ||
            AudioUnitGetParameter(au, 17 + retained * 10, 0, 0, &frequency) || frequency != (subset == 1 ? 90 : 80) ||
            AudioUnitGetParameter(au, 19 + retained * 10, 0, 0, &order) || order != (subset == 1 ? 3 : 4))
            return fail("retained HP source mismatch", -1);
        for (unsigned filter = 0; filter < 50; filter++) {
            float value = filter == retained ? 3 : 0;
            if (AudioUnitSetParameter(au, 14 + filter * 10, 0, 0, value, 0)) return fail("subset state", -1);
            AudioUnitParameterValue actual;
            if (AudioUnitGetParameter(au, 14 + filter * 10, 0, 0, &actual) || actual != value)
                return fail("subset readback", -1);
        }
    }
    AudioStreamBasicDescription format = {
        .mSampleRate = 48000, .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagIsNonInterleaved,
        .mBytesPerPacket = sizeof(float), .mFramesPerPacket = 1,
        .mBytesPerFrame = sizeof(float), .mChannelsPerFrame = CHANNELS, .mBitsPerChannel = 32
    };
    for (unsigned scope = kAudioUnitScope_Input; scope <= kAudioUnitScope_Output; scope++) {
        status = AudioUnitSetProperty(au, kAudioUnitProperty_StreamFormat, scope, 0, &format, sizeof(format));
        if (status) return fail("stream format", status);
        AudioStreamBasicDescription actual = {0}; UInt32 size = sizeof(actual);
        status = AudioUnitGetProperty(au, kAudioUnitProperty_StreamFormat, scope, 0, &actual, &size);
        if (status || memcmp(&format, &actual, sizeof(format))) return fail("format readback", status);
    }
    UInt32 maxframes = BLOCK;
    status = AudioUnitSetProperty(au, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxframes, sizeof(maxframes));
    if (status) return fail("maximum frames", status);
    struct input input = { .lane = lane, .amplitude = amplitude };
    float *file_input = NULL;
    if (strcmp(argv[6], "impulse")) {
        file = fopen(argv[6], "rb");
        if (!file) return fail("input open", -1);
        file_input = malloc(CHANNELS * FRAMES * sizeof(float));
        if (!file_input || fread(file_input, sizeof(float), CHANNELS * FRAMES, file) != CHANNELS * FRAMES || fgetc(file) != EOF)
            return fail("input length", -1);
        fclose(file);
        for (unsigned i = 0; i < CHANNELS * FRAMES; i++)
            if (!isfinite(file_input[i]) || fabsf(file_input[i]) > 0.25f) return fail("input bound", -1);
        input.file_samples = file_input;
    }
    AURenderCallbackStruct cb = { .inputProc = supply, .inputProcRefCon = &input };
    status = AudioUnitSetProperty(au, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof(cb));
    if (status) return fail("memory callback", status);
    status = AudioUnitInitialize(au);
    if (status) return fail("initialize effect", status);
    if (subset) {
        for (unsigned filter = 0; filter < 50; filter++) {
            float expected = filter == (subset == 1 ? 0u : 5u) ? 3 : 0;
            AudioUnitParameterValue actual;
            if (AudioUnitGetParameter(au, 14 + filter * 10, 0, 0, &actual) || actual != expected)
                return fail("initialized subset readback", -1);
        }
    }
    status = AudioUnitSetParameter(au, 3, kAudioUnitScope_Global, 0, 1.0f, 0);
    if (status) return fail("explicit SystemVolume", status);
    status = AudioUnitSetParameter(au, 0, kAudioUnitScope_Global, 0, (float)bypass, 0);
    if (status) return fail("bypass control", status);
    AudioUnitParameterValue volume = -1, actual_bypass = -1;
    if (AudioUnitGetParameter(au, 3, kAudioUnitScope_Global, 0, &volume) || volume != 1.0f ||
        AudioUnitGetParameter(au, 0, kAudioUnitScope_Global, 0, &actual_bypass) || actual_bypass != bypass)
        return fail("control readback", -1);
    Float64 latency = -1, tail = -1; UInt32 size = sizeof(latency);
    OSStatus latency_status = AudioUnitGetProperty(au, kAudioUnitProperty_Latency, kAudioUnitScope_Global, 0, &latency, &size);
    size = sizeof(tail);
    OSStatus tail_status = AudioUnitGetProperty(au, kAudioUnitProperty_TailTime, kAudioUnitScope_Global, 0, &tail, &size);
    AudioBufferList *buffers = calloc(1, offsetof(AudioBufferList, mBuffers) + CHANNELS * sizeof(AudioBuffer));
    float *response = calloc(CHANNELS * FRAMES, sizeof(float));
    float block[CHANNELS][BLOCK];
    if (!buffers || !response) return 9;
    buffers->mNumberBuffers = CHANNELS;
    for (unsigned frame = 0; frame < FRAMES; frame += BLOCK) {
        for (unsigned ch = 0; ch < CHANNELS; ch++) {
            buffers->mBuffers[ch].mNumberChannels = 1;
            buffers->mBuffers[ch].mDataByteSize = BLOCK * sizeof(float);
            buffers->mBuffers[ch].mData = block[ch];
            memset(block[ch], 0, sizeof(block[ch]));
        }
        AudioTimeStamp stamp = { .mSampleTime = frame, .mFlags = kAudioTimeStampSampleTimeValid };
        AudioUnitRenderActionFlags flags = 0;
        status = AudioUnitRender(au, &flags, &stamp, 0, BLOCK, buffers);
        if (status) return fail("memory render", status);
        for (unsigned ch = 0; ch < CHANNELS; ch++) {
            if (buffers->mBuffers[ch].mDataByteSize != BLOCK * sizeof(float) || !buffers->mBuffers[ch].mData) return 10;
            const float *samples = buffers->mBuffers[ch].mData;
            for (unsigned i = 0; i < BLOCK; i++) {
                if (!isfinite(samples[i])) return fail("nonfinite output", -1);
                response[ch * FRAMES + frame + i] = samples[i];
            }
        }
    }
    if (input.impulses != (file_input ? 0u : 1u)) return fail("input impulse count", (OSStatus)input.impulses);
    status = AudioUnitUninitialize(au);
    if (status) return fail("uninitialize", status);
    status = AudioComponentInstanceDispose(au);
    if (status) return fail("dispose", status);
    file = fopen(argv[2], "wb");
    if (!file || fwrite(response, sizeof(float), CHANNELS * FRAMES, file) != CHANNELS * FRAMES || fclose(file)) return 11;
    printf("{\"sample_rate\":48000,\"channels\":4,\"frames\":%u,\"impulse_frame\":%u,\"lane\":%u,\"amplitude\":%.9g,\"bypass\":%u,\"system_volume\":%.9g,\"callbacks\":%u,\"impulses\":%u,\"latency_status\":%d,\"latency\":%.9g,\"tail_status\":%d,\"tail\":%.9g}\n",
           FRAMES, IMPULSE, lane, amplitude, bypass, volume, input.calls, input.impulses,
           (int)latency_status, latency, (int)tail_status, tail);
    free(response); free(buffers); free(file_input);
    return 0;
}
