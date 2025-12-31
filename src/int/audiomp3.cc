#include "int/audiomp3.h"

#include <cstdio>

#include <SDL.h>

#define MINIMP3_IMPLEMENTATION
#include "lib/minimp3.h"
#include "lib/minimp3_ex.h"

#include "int/memdbg.h"
#include "plib/gnw/debug.h"

namespace fallout {

// Target format expected by the sound system
#define AUDIOMP3_TARGET_RATE 22050
#define AUDIOMP3_TARGET_CHANNELS 2

typedef enum AudioMp3Flags {
    AUDIO_MP3_IN_USE = 0x01,
} AudioMp3Flags;

typedef struct AudioMp3 {
    int flags;
    mp3dec_ex_t decoder;
    SDL_AudioStream* stream;
    int64_t fileSize;      // resampled size in bytes
    int64_t position;      // current position in resampled bytes
    int srcChannels;
    int srcSampleRate;
} AudioMp3;

static AudioMp3* audiomp3Files = NULL;
static int numAudioMp3 = 0;

int audiomp3Open(const char* fname, int flags)
{
    // Find a free slot or allocate a new one
    int index;
    for (index = 0; index < numAudioMp3; index++) {
        if ((audiomp3Files[index].flags & AUDIO_MP3_IN_USE) == 0) {
            break;
        }
    }

    if (index == numAudioMp3) {
        if (audiomp3Files != NULL) {
            audiomp3Files = (AudioMp3*)myrealloc(audiomp3Files, sizeof(*audiomp3Files) * (numAudioMp3 + 1), __FILE__, __LINE__);
        } else {
            audiomp3Files = (AudioMp3*)mymalloc(sizeof(*audiomp3Files), __FILE__, __LINE__);
        }
        memset(&audiomp3Files[numAudioMp3], 0, sizeof(AudioMp3));
        numAudioMp3++;
    }

    AudioMp3* mp3File = &audiomp3Files[index];
    memset(mp3File, 0, sizeof(AudioMp3));

    int ret = mp3dec_ex_open(&mp3File->decoder, fname, MP3D_SEEK_TO_SAMPLE);
    if (ret != 0) {
        debug_printf("audiomp3Open: Failed to open %s, error %d\n", fname, ret);
        return -1;
    }

    mp3File->srcChannels = mp3File->decoder.info.channels;
    mp3File->srcSampleRate = mp3File->decoder.info.hz;

    // Create audio stream for resampling if needed
    if (mp3File->srcSampleRate != AUDIOMP3_TARGET_RATE || mp3File->srcChannels != AUDIOMP3_TARGET_CHANNELS) {
        mp3File->stream = SDL_NewAudioStream(
            AUDIO_S16, mp3File->srcChannels, mp3File->srcSampleRate,
            AUDIO_S16, AUDIOMP3_TARGET_CHANNELS, AUDIOMP3_TARGET_RATE);
        if (mp3File->stream == NULL) {
            debug_printf("audiomp3Open: Failed to create audio stream: %s\n", SDL_GetError());
            mp3dec_ex_close(&mp3File->decoder);
            return -1;
        }
        debug_printf("audiomp3Open: Resampling %dHz %dch -> %dHz %dch\n",
            mp3File->srcSampleRate, mp3File->srcChannels,
            AUDIOMP3_TARGET_RATE, AUDIOMP3_TARGET_CHANNELS);
    } else {
        mp3File->stream = NULL;
    }

    mp3File->flags = AUDIO_MP3_IN_USE;

    // Calculate resampled file size
    // Original: decoder.samples is total sample count (frames * channels)
    int64_t srcFrames = mp3File->decoder.samples / mp3File->srcChannels;
    int64_t dstFrames = (srcFrames * AUDIOMP3_TARGET_RATE) / mp3File->srcSampleRate;
    mp3File->fileSize = dstFrames * AUDIOMP3_TARGET_CHANNELS * sizeof(mp3d_sample_t);
    mp3File->position = 0;

    return index + 1;
}

int audiomp3CloseFile(int fileHandle)
{
    if (fileHandle <= 0 || fileHandle > numAudioMp3) {
        return -1;
    }

    AudioMp3* mp3File = &audiomp3Files[fileHandle - 1];

    if (mp3File->stream != NULL) {
        SDL_FreeAudioStream(mp3File->stream);
    }
    mp3dec_ex_close(&mp3File->decoder);

    memset(mp3File, 0, sizeof(AudioMp3));

    return 0;
}

int audiomp3Read(int fileHandle, void* buffer, unsigned int size)
{
    if (fileHandle <= 0 || fileHandle > numAudioMp3) {
        return -1;
    }

    AudioMp3* mp3File = &audiomp3Files[fileHandle - 1];

    if (mp3File->stream == NULL) {
        // No resampling needed - direct read
        size_t samplesToRead = size / sizeof(mp3d_sample_t);
        size_t samplesRead = mp3dec_ex_read(&mp3File->decoder, (mp3d_sample_t*)buffer, samplesToRead);
        int bytesRead = samplesRead * sizeof(mp3d_sample_t);
        mp3File->position += bytesRead;
        return bytesRead;
    }

    // Resampling path
    int totalBytesOut = 0;
    unsigned char* outBuf = (unsigned char*)buffer;

    while ((unsigned int)totalBytesOut < size) {
        // Try to get resampled data from stream
        int available = SDL_AudioStreamAvailable(mp3File->stream);
        if (available > 0) {
            int toGet = size - totalBytesOut;
            if (toGet > available) {
                toGet = available;
            }
            int got = SDL_AudioStreamGet(mp3File->stream, outBuf + totalBytesOut, toGet);
            if (got > 0) {
                totalBytesOut += got;
            }
            continue;
        }

        // Need to decode more MP3 data
        mp3d_sample_t decodeBuf[1152 * 2]; // Max samples per MP3 frame * stereo
        size_t samplesToRead = sizeof(decodeBuf) / sizeof(mp3d_sample_t);
        size_t samplesRead = mp3dec_ex_read(&mp3File->decoder, decodeBuf, samplesToRead);

        if (samplesRead == 0) {
            // EOF - flush the stream
            SDL_AudioStreamFlush(mp3File->stream);
            int available2 = SDL_AudioStreamAvailable(mp3File->stream);
            if (available2 > 0) {
                int toGet = size - totalBytesOut;
                if (toGet > available2) {
                    toGet = available2;
                }
                int got = SDL_AudioStreamGet(mp3File->stream, outBuf + totalBytesOut, toGet);
                if (got > 0) {
                    totalBytesOut += got;
                }
            }
            break;
        }

        // Push decoded data to resampler
        int bytesDecoded = samplesRead * sizeof(mp3d_sample_t);
        if (SDL_AudioStreamPut(mp3File->stream, decodeBuf, bytesDecoded) < 0) {
            debug_printf("audiomp3Read: SDL_AudioStreamPut failed: %s\n", SDL_GetError());
            break;
        }
    }

    mp3File->position += totalBytesOut;
    return totalBytesOut;
}

long audiomp3Seek(int fileHandle, long offset, int origin)
{
    if (fileHandle <= 0 || fileHandle > numAudioMp3) {
        return -1;
    }

    AudioMp3* mp3File = &audiomp3Files[fileHandle - 1];

    int64_t newPos;
    switch (origin) {
    case SEEK_SET:
        newPos = offset;
        break;
    case SEEK_CUR:
        newPos = mp3File->position + offset;
        break;
    case SEEK_END:
        newPos = mp3File->fileSize + offset;
        break;
    default:
        newPos = offset;
        break;
    }

    if (newPos < 0) {
        newPos = 0;
    }
    if (newPos > mp3File->fileSize) {
        newPos = mp3File->fileSize;
    }

    // Convert resampled byte position to source sample position
    int64_t dstFrames = newPos / (AUDIOMP3_TARGET_CHANNELS * sizeof(mp3d_sample_t));
    int64_t srcFrames = (dstFrames * mp3File->srcSampleRate) / AUDIOMP3_TARGET_RATE;
    uint64_t srcSamplePos = srcFrames * mp3File->srcChannels;

    int ret = mp3dec_ex_seek(&mp3File->decoder, srcSamplePos);
    if (ret != 0) {
        debug_printf("audiomp3Seek: seek failed with error %d\n", ret);
        return -1;
    }

    // Clear resampler state
    if (mp3File->stream != NULL) {
        SDL_AudioStreamClear(mp3File->stream);
    }

    mp3File->position = newPos;

    return mp3File->position;
}

long audiomp3FileSize(int fileHandle)
{
    if (fileHandle <= 0 || fileHandle > numAudioMp3) {
        return -1;
    }

    AudioMp3* mp3File = &audiomp3Files[fileHandle - 1];
    return mp3File->fileSize;
}

long audiomp3Tell(int fileHandle)
{
    if (fileHandle <= 0 || fileHandle > numAudioMp3) {
        return -1;
    }

    AudioMp3* mp3File = &audiomp3Files[fileHandle - 1];
    return (long)mp3File->position;
}

int audiomp3Write(int handle, const void* buf, unsigned int size)
{
    debug_printf("audiomp3Write shouldn't be ever called\n");
    return -1;
}

void audiomp3Close()
{
    if (audiomp3Files != NULL) {
        for (int i = 0; i < numAudioMp3; i++) {
            if (audiomp3Files[i].flags & AUDIO_MP3_IN_USE) {
                if (audiomp3Files[i].stream != NULL) {
                    SDL_FreeAudioStream(audiomp3Files[i].stream);
                }
                mp3dec_ex_close(&audiomp3Files[i].decoder);
            }
        }
        myfree(audiomp3Files, __FILE__, __LINE__);
    }

    numAudioMp3 = 0;
    audiomp3Files = NULL;
}

} // namespace fallout
