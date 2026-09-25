using System.Security.Cryptography;
using System.Text.Json;
using ClassroomMate.Core;
using Microsoft.Win32;

namespace ClassroomMate.Windows;

public sealed record Preferences(string DisplayName = "", string AudioOutputName = "");
public sealed class Storage
{
    public static string Root => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ClassClaw", "ClassroomMate");
    public string LedgerPath => Path.Combine(Root, "commands.json");
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
        get { using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"); return key?.GetValue("ClassroomMate") is not null; }
        set
        {
            using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
            if (value) key.SetValue("ClassroomMate", $"\"{Environment.ProcessPath}\" --tray");
            else key.DeleteValue("ClassroomMate", false);
        }
    }
}
