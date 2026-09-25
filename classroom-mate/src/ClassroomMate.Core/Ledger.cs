using System.Text.Json;

namespace ClassroomMate.Core;

public sealed record LedgerEntry(DateTimeOffset ExpiresAt, Receipt Receipt);

// Contains only command IDs/outcomes, never sentences, recipients or credentials.
public sealed class Ledger
{
    readonly string path;
    readonly object gate = new();
    Dictionary<string, LedgerEntry> entries;
    public Ledger(string path)
    {
        this.path = path;
        entries = File.Exists(path) ? JsonSerializer.Deserialize<Dictionary<string, LedgerEntry>>(File.ReadAllText(path), Wire.Json)
            ?? throw new IOException("去重账本损坏") : [];
        foreach (var id in entries.Keys.ToArray())
            if (entries[id].Receipt.State == "started") entries[id] = entries[id] with
            { Receipt = new(id, "unknown", ErrorCode: "PROCESS_INTERRUPTED", ErrorMessage: "上次运行中断，未重播") };
        Save();
    }
    public Receipt? Find(string id) { lock (gate) return entries.GetValueOrDefault(id)?.Receipt; }
    public Receipt? Reserve(Command command, DateTimeOffset now)
    {
        lock (gate)
        {
            if (entries.TryGetValue(command.Id, out var found)) return found.Receipt;
            foreach (var id in entries.Where(p => p.Value.ExpiresAt < now.AddDays(-1)).Select(p => p.Key).ToArray()) entries.Remove(id);
            if (entries.Count >= 2000) throw new TerminalException("LEDGER_FULL", "去重账本已满，暂停执行新命令");
            entries.Add(command.Id, new(command.ExpiresAt, new(command.Id, "started")));
            try { Save(); } catch { entries.Remove(command.Id); throw; }
            return null;
        }
    }
    public void Complete(Receipt receipt)
    {
        lock (gate)
        {
            if (!entries.TryGetValue(receipt.CommandId, out var found)) return;
            entries[receipt.CommandId] = found with { Receipt = receipt };
            Save();
        }
    }
    void Save() => AtomicFile.Write(path, JsonSerializer.SerializeToUtf8Bytes(entries, Wire.Json));
}

public static class AtomicFile
{
    public static void Write(string path, byte[] data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + ".tmp";
        using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
        { stream.Write(data); stream.Flush(true); }
        File.Move(temporary, path, true);
    }
}
