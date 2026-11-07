using System.Security;
using System.Security.Cryptography;
using System.Text.Json;
using ClassroomMate.Core;
using Microsoft.Win32;

namespace ClassroomMate.Windows;

public sealed record Preferences(string DisplayName = "", string AudioOutputName = "",
    string? CameraIdentifier = null, string? MicrophoneIdentifier = null, int VideoHeight = 720)
{
    [System.Text.Json.Serialization.JsonIgnore]
    public int CaptureHeight => VideoHeight is 360 or 480 or 720 or 1080 ? VideoHeight : 720;
}
public sealed class Storage
{
    static readonly object DiagnosticLock = new();
    public static string Root => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ClassClaw", "ClassroomMate");
    public static string DiagnosticsPath => Path.Combine(Root, "diagnostics.log");
    public string LedgerPath => Path.Combine(Root, "commands.json");

    public static void Diagnostic(string stage, string code, Guid? requestId = null)
    {
        if (code.Length > 64 || code.Any(character => !char.IsAsciiLetterOrDigit(character) && character != '_'))
            code = "UNKNOWN";
        var entry = $"{DateTimeOffset.Now:O} {stage} {code}" + (requestId is Guid id ? $" request_id={id}" : "") + Environment.NewLine;
        lock (DiagnosticLock)
        {
            try
            {
                Directory.CreateDirectory(Root);
                var file = new FileInfo(DiagnosticsPath);
                if (file.Exists && file.Length >= 65536) File.WriteAllText(file.FullName, entry);
                else File.AppendAllText(file.FullName, entry);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or SecurityException) { }
        }
    }
    public Pairing? LoadPairing()
    {
        var path = Path.Combine(Root, "device.dpapi");
        if (!File.Exists(path)) return null;
        var bytes = ProtectedData.Unprotect(File.ReadAllBytes(path), null, DataProtectionScope.CurrentUser);
        try { return JsonSerializer.Deserialize<Pairing>(bytes, Wire.Json); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }
    public void SavePairing(Pairing pairing)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(pairing, Wire.Json);
        try { AtomicFile.Write(Path.Combine(Root, "device.dpapi"), ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser)); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }
    public Preferences LoadPreferences() => File.Exists(Path.Combine(Root, "settings.json"))
        ? JsonSerializer.Deserialize<Preferences>(File.ReadAllText(Path.Combine(Root, "settings.json")), Wire.Json) ?? new() : new();
    public void SavePreferences(Preferences value) => AtomicFile.Write(Path.Combine(Root, "settings.json"), JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json));
    public static bool StartupEnabled
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
                return key?.GetValue("ClassroomMate") is not null;
            }
            catch (Exception exception) when (exception is UnauthorizedAccessException or SecurityException or IOException)
            {
                // Group Policy may block the Run key; that must not prevent the tray app from starting or pairing.
                return false;
            }
        }
        set
        {
            using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
            if (value) key.SetValue("ClassroomMate", $"\"{Environment.ProcessPath}\" --tray");
            else key.DeleteValue("ClassroomMate", false);
        }
    }
}
