#include "AtomicJsonWriter.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

#include <sstream>

namespace
{
    std::string Win32ErrorMessage(DWORD code)
    {
        LPSTR buffer = NULL;
        const DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER |
                            FORMAT_MESSAGE_FROM_SYSTEM |
                            FORMAT_MESSAGE_IGNORE_INSERTS;
        const DWORD length = FormatMessageA(
            flags,
            NULL,
            code,
            MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
            reinterpret_cast<LPSTR>(&buffer),
            0,
            NULL);
        std::string message;
        if (length > 0 && buffer != NULL)
            message.assign(buffer, length);
        else
        {
            std::ostringstream stream;
            stream << "Win32 error " << code;
            message = stream.str();
        }
        if (buffer != NULL)
            LocalFree(buffer);
        return message;
    }

    std::wstring JoinPath(const std::wstring& directory, const std::wstring& fileName)
    {
        if (directory.empty())
            return fileName;
        const wchar_t last = directory[directory.size() - 1];
        if (last == L'\\' || last == L'/')
            return directory + fileName;
        return directory + L"\\" + fileName;
    }
}

namespace KenshiAgentTelemetry
{
    bool EnsureOutputDirectory(const std::wstring& directory)
    {
        if (directory.empty())
            return false;
        const DWORD attributes = GetFileAttributesW(directory.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES)
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        if (CreateDirectoryW(directory.c_str(), NULL))
            return true;
        return GetLastError() == ERROR_ALREADY_EXISTS;
    }

    std::wstring ResolveTelemetryDirectory()
    {
        wchar_t explicitDirectory[32768];
        const DWORD explicitLength = GetEnvironmentVariableW(
            L"KENSHI_AGENT_TELEMETRY_DIR",
            explicitDirectory,
            static_cast<DWORD>(sizeof(explicitDirectory) / sizeof(explicitDirectory[0])));
        if (explicitLength > 0 && explicitLength < (sizeof(explicitDirectory) / sizeof(explicitDirectory[0])))
            return std::wstring(explicitDirectory, explicitLength);

        wchar_t localAppData[32768];
        const DWORD localLength = GetEnvironmentVariableW(
            L"LOCALAPPDATA",
            localAppData,
            static_cast<DWORD>(sizeof(localAppData) / sizeof(localAppData[0])));
        if (localLength > 0 && localLength < (sizeof(localAppData) / sizeof(localAppData[0])))
            return JoinPath(std::wstring(localAppData, localLength), L"KenshiAgent");

        return L".\\KenshiAgent";
    }

    bool AtomicWriteUtf8(
        const std::wstring& directory,
        const std::wstring& fileName,
        const std::string& payload,
        std::string& errorOut)
    {
        if (!EnsureOutputDirectory(directory))
        {
            errorOut = "Could not create or access telemetry directory.";
            return false;
        }

        const std::wstring targetPath = JoinPath(directory, fileName);
        std::wostringstream temporaryName;
        temporaryName << fileName << L"." << GetCurrentProcessId() << L".tmp";
        const std::wstring temporaryPath = JoinPath(directory, temporaryName.str());

        HANDLE file = CreateFileW(
            temporaryPath.c_str(),
            GENERIC_WRITE,
            FILE_SHARE_READ,
            NULL,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            NULL);
        if (file == INVALID_HANDLE_VALUE)
        {
            errorOut = Win32ErrorMessage(GetLastError());
            return false;
        }

        DWORD written = 0;
        const BOOL writeOk = WriteFile(
            file,
            payload.data(),
            static_cast<DWORD>(payload.size()),
            &written,
            NULL);
        const BOOL flushOk = FlushFileBuffers(file);
        CloseHandle(file);

        if (!writeOk || written != payload.size() || !flushOk)
        {
            errorOut = Win32ErrorMessage(GetLastError());
            DeleteFileW(temporaryPath.c_str());
            return false;
        }

        if (!MoveFileExW(
                temporaryPath.c_str(),
                targetPath.c_str(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        {
            errorOut = Win32ErrorMessage(GetLastError());
            DeleteFileW(temporaryPath.c_str());
            return false;
        }
        return true;
    }
}
