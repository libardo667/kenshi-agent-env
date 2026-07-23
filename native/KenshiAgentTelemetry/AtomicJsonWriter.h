#pragma once

#include <string>

namespace KenshiAgentTelemetry
{
    bool EnsureOutputDirectory(const std::wstring& directory);
    bool AtomicWriteUtf8(
        const std::wstring& directory,
        const std::wstring& fileName,
        const std::string& payload,
        std::string& errorOut);
    bool ReadUtf8Bounded(
        const std::wstring& directory,
        const std::wstring& fileName,
        unsigned int maximumBytes,
        std::string& payloadOut,
        std::string& errorOut);
    std::wstring ResolveTelemetryDirectory();
}
