#include "wifi_board.h"
#include "application.h"
#include "button.h"
#include "audio_codec.h"

#include <esp_log.h>
#include <esp_heap_caps.h>
#include <driver/i2s_std.h>
#include <vector>
#include <cmath>
#include <algorithm>
#include <mutex>

#define TAG "YoloUno"

// Shared I2S clocks, INMP441 input and MAX98357A output
#define MIC_I2S_BCLK GPIO_NUM_6
#define MIC_I2S_WS   GPIO_NUM_10
#define MIC_I2S_DATA GPIO_NUM_17
#define SPK_I2S_DATA GPIO_NUM_7

class YoloUnoAudioCodec : public AudioCodec {
private:
    std::mutex tx_mutex_;
    std::mutex rx_mutex_;

public:
    YoloUnoAudioCodec(int sample_rate, gpio_num_t bclk, gpio_num_t ws, gpio_num_t dout, gpio_num_t din) {
        duplex_ = true;
        input_sample_rate_ = sample_rate;
        output_sample_rate_ = sample_rate;
        input_channels_ = 1;
        output_channels_ = 1;

        i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
        chan_cfg.dma_desc_num = AUDIO_CODEC_DMA_DESC_NUM;
        chan_cfg.dma_frame_num = AUDIO_CODEC_DMA_FRAME_NUM;
        chan_cfg.auto_clear_after_cb = true;
        chan_cfg.auto_clear_before_cb = false;
        chan_cfg.intr_priority = 0;
        ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &tx_handle_, &rx_handle_));

        // Shared I2S Philips Standard stereo 32-bit slot configuration
        i2s_std_config_t std_cfg = {
            .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(static_cast<uint32_t>(sample_rate)),
            .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
            .gpio_cfg = {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = bclk,
                .ws = ws,
                .dout = dout,
                .din = din,
                .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false }
            }
        };
        std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;

        ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_handle_, &std_cfg));
        ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle_, &std_cfg));
        ESP_LOGI(TAG, "Duplex stereo I2S ready: BCLK=GPIO%d, WS=GPIO%d, DOUT=GPIO%d, DIN=GPIO%d", bclk, ws, dout, din);
    }

    ~YoloUnoAudioCodec() override {
        if (rx_handle_ != nullptr) {
            i2s_channel_disable(rx_handle_);
        }
        if (tx_handle_ != nullptr) {
            i2s_channel_disable(tx_handle_);
        }
    }

    int Write(const int16_t* data, int samples) override {
        std::lock_guard<std::mutex> lock(tx_mutex_);
        if (tx_handle_ == nullptr || samples <= 0) return 0;
        if (!output_enabled_) {
            EnableOutput(true);
        }

        // Stereo expansion: duplicate mono sample to both Left and Right channels for MAX98357A
        std::vector<int32_t> buffer(samples * 2);
        int32_t volume_factor = (output_volume_ * output_volume_ * 65536) / 10000;
        for (int i = 0; i < samples; i++) {
            int64_t v = (int64_t)data[i] * volume_factor;
            int32_t s32 = (int32_t)std::clamp<int64_t>(v, INT32_MIN, INT32_MAX);
            buffer[i * 2] = s32;     // Left slot
            buffer[i * 2 + 1] = s32; // Right slot
        }

        size_t bytes_written = 0;
        esp_err_t err = i2s_channel_write(tx_handle_, buffer.data(), buffer.size() * sizeof(int32_t), &bytes_written, portMAX_DELAY);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "i2s write failed: %s", esp_err_to_name(err));
            return 0;
        }
        return samples;
    }

    int Read(int16_t* dest, int samples) override {
        std::lock_guard<std::mutex> lock(rx_mutex_);
        if (rx_handle_ == nullptr || samples <= 0) return 0;
        if (!input_enabled_) {
            EnableInput(true);
        }

        std::vector<int32_t> buffer(samples * 2);
        size_t bytes_read = 0;
        esp_err_t err = i2s_channel_read(rx_handle_, buffer.data(), buffer.size() * sizeof(int32_t), &bytes_read, portMAX_DELAY);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "i2s read failed: %s", esp_err_to_name(err));
            return 0;
        }

        // INMP441 with L/R grounded sends 24-bit data on Left channel (index 0, 2, 4...)
        // Bit 31 is sign bit, bits 31..8 are the 24 data bits.
        for (int i = 0; i < samples; i++) {
            int32_t raw_l = buffer[i * 2];
            int32_t raw_r = buffer[i * 2 + 1];
            int32_t raw = (std::abs(raw_l) >= std::abs(raw_r)) ? raw_l : raw_r;
            int32_t val = raw >> 14;
            dest[i] = (int16_t)std::clamp<int32_t>(val, -32768, 32767);
        }
        return samples;
    }
};

static YoloUnoAudioCodec g_audio_codec(16000, MIC_I2S_BCLK, MIC_I2S_WS, SPK_I2S_DATA, MIC_I2S_DATA);

class YoloUnoBoard : public WifiBoard {
private:
    Button boot_button_{GPIO_NUM_0};

public:
    YoloUnoBoard() {
        boot_button_.OnClick([]() {
            Application::GetInstance().ToggleChatState();
        });
    }

    AudioCodec* GetAudioCodec() override {
        return &g_audio_codec;
    }


};

DECLARE_BOARD(YoloUnoBoard);
