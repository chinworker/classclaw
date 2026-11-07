using System.Net.Http.Json;
using System.Security.Cryptography;

namespace ClassroomMate.Core;

public sealed record UpdateRelease(Version Version, long Size, string Sha256);

public sealed class UpdateClient : IDisposable
{
    const string DirectoryPath = "/downloads/classroom-mate";
    const string InstallerName = "ClassroomMate-Setup-win-x64.exe";
    const long MaxInstallerSize = 250L * 1024 * 1024;
    readonly HttpClient http;

    public UpdateClient(Uri server, HttpMessageHandler? handler = null)
    {
        http = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }) { BaseAddress = server };
    }

    sealed record Manifest(string Version, long Size, string Sha256);

    public async Task<UpdateRelease> Latest(CancellationToken cancellation)
    {
        using var response = await http.GetAsync(DirectoryPath + "/latest/release.json",
            HttpCompletionOption.ResponseHeadersRead, cancellation);
        response.EnsureSuccessStatusCode();
        await response.Content.LoadIntoBufferAsync(4096, cancellation);
        var manifest = await response.Content.ReadFromJsonAsync<Manifest>(cancellation);
        if (manifest is null || !Version.TryParse(manifest.Version, out var version) ||
            version.Revision is < 1 or > 65534 || manifest.Size is <= 0 or > MaxInstallerSize ||
            manifest.Sha256 is not { Length: 64 } || !manifest.Sha256.All(Uri.IsHexDigit))
            throw new InvalidDataException("服务器返回了无效的版本信息。");
        return new UpdateRelease(version, manifest.Size, manifest.Sha256);
    }

    public async Task<string> Download(UpdateRelease release, string directory, CancellationToken cancellation)
    {
        using var response = await http.GetAsync($"{DirectoryPath}/build-{release.Version.Revision}/{InstallerName}",
            HttpCompletionOption.ResponseHeadersRead, cancellation);
        response.EnsureSuccessStatusCode();
        if (response.Content.Headers.ContentLength is long length && length != release.Size)
            throw new InvalidDataException("安装包大小与发布信息不符。");

        Directory.CreateDirectory(directory);
        var temp = Path.Combine(directory, Path.GetRandomFileName());
        var target = Path.Combine(directory, InstallerName);
        try
        {
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            await using var source = await response.Content.ReadAsStreamAsync(cancellation);
            await using (var destination = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true))
            {
                var buffer = new byte[81920];
                long received = 0;
                int read;
                while ((read = await source.ReadAsync(buffer.AsMemory(), cancellation)) != 0)
                {
                    received += read;
                    if (received > release.Size)
                        throw new InvalidDataException("安装包大小与发布信息不符。");
                    hash.AppendData(buffer, 0, read);
                    await destination.WriteAsync(buffer.AsMemory(0, read), cancellation);
                }
                if (received != release.Size ||
                    !Convert.ToHexString(hash.GetHashAndReset()).Equals(release.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("安装包校验失败，请重新下载。");
            }
            File.Move(temp, target, overwrite: true);
            return target;
        }
        finally { File.Delete(temp); }
    }

    public void Dispose() => http.Dispose();
}
