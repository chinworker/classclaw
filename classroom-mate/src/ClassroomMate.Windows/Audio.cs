using System.Runtime.InteropServices;
using System.Speech.Synthesis;
using ClassroomMate.Core;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace ClassroomMate.Windows;

public sealed class Audio
{
    readonly object gate = new();
    string selected = "";
    Restore? pending;
    public bool HasChineseVoice { get { try { using var synth = new SpeechSynthesizer(); return synth.GetInstalledVoices().Any(v => v.Enabled && v.VoiceInfo.Culture.TwoLetterISOLanguageName == "zh"); } catch { return false; } } }
    public void Select(string name) { using var device = Open(name); lock (gate) selected = name; }
    MMDevice Open(string? name = null)
    {
        var value = name ?? selected;
        var item = Devices.Speakers().SingleOrDefault(s => s.Name == value)
            ?? throw new TerminalException("DEVICE_OUTPUT_UNAVAILABLE", "请在网页选择可用的教室扬声器");
        using var enumerator = new MMDeviceEnumerator();
        return enumerator.GetDevice(item.NativeName);
    }
    public (int? Volume, bool Muted) Read()
    {
        try { using var device = Open(); return ((int)Math.Round(device.AudioEndpointVolume.MasterVolumeLevelScalar * 100), device.AudioEndpointVolume.Mute); }
        catch { return (null, false); }
    }
    public void Set(int? level, bool? mute, bool restore, int ceiling)
    {
        if (level > ceiling) throw new TerminalException("VOLUME_ABOVE_CEILING", "音量超过服务端上限");
        lock (gate)
        {
            pending?.Dispose(); pending = null;
            var device = Open();
            if (restore) pending = new Restore(device, level, mute);
            else { using (device) { if (level.HasValue) device.AudioEndpointVolume.MasterVolumeLevelScalar = level.Value / 100f; if (mute.HasValue) device.AudioEndpointVolume.Mute = mute.Value; } }
        }
    }
    public IDisposable? Begin(int? level, int ceiling)
    {
        lock (gate)
        {
            if (level > ceiling) throw new TerminalException("VOLUME_ABOVE_CEILING", "音量超过服务端上限");
            var old = pending; pending = null;
            if (old is not null)
            {
                try { if (level.HasValue) old.Apply(level.Value); return old; }
                catch { old.Dispose(); throw; }
            }
            return level.HasValue ? new Restore(Open(), level, null) : null;
        }
    }
    public void RestorePending() { lock (gate) { pending?.Dispose(); pending = null; } }
    sealed class Restore : IDisposable
    {
        readonly MMDevice device;
        readonly Guid context = Guid.NewGuid();
        readonly float old; readonly bool oldMute;
        float applied; bool appliedMute; volatile bool changed; bool disposed;
        public Restore(MMDevice device, int? level, bool? mute)
        {
            this.device = device;
            try
            {
                old = device.AudioEndpointVolume.MasterVolumeLevelScalar; oldMute = device.AudioEndpointVolume.Mute;
                applied = level / 100f ?? old; appliedMute = mute ?? oldMute;
                device.AudioEndpointVolume.NotificationGuid = context;
                device.AudioEndpointVolume.OnVolumeNotification += Notification;
                device.AudioEndpointVolume.MasterVolumeLevelScalar = applied;
                if (mute.HasValue) device.AudioEndpointVolume.Mute = appliedMute;
            }
            catch
            {
                try { device.AudioEndpointVolume.OnVolumeNotification -= Notification; }
                finally { device.Dispose(); }
                throw;
            }
        }
        void Notification(AudioVolumeNotificationData data) { if (data.EventContext != context) changed = true; }
        public void Apply(int level) { applied = level / 100f; device.AudioEndpointVolume.MasterVolumeLevelScalar = applied; }
        public void Dispose()
        {
            if (disposed) return; disposed = true;
            try
            {
                device.AudioEndpointVolume.OnVolumeNotification -= Notification;
                if (!changed && Math.Abs(device.AudioEndpointVolume.MasterVolumeLevelScalar - applied) < .005f && device.AudioEndpointVolume.Mute == appliedMute)
                { device.AudioEndpointVolume.MasterVolumeLevelScalar = old; device.AudioEndpointVolume.Mute = oldMute; }
            }
            catch (COMException) { }
            finally { device.Dispose(); }
        }
    }
    public Task Speak(string text, CancellationToken token) => Task.Run(async () =>
    {
        token.ThrowIfCancellationRequested();
        using var synth = new SpeechSynthesizer();
        var voice = synth.GetInstalledVoices().FirstOrDefault(v => v.Enabled && v.VoiceInfo.Culture.TwoLetterISOLanguageName == "zh")
            ?? throw new TerminalException("CHINESE_TTS_UNAVAILABLE", "系统未安装中文语音");
        using var device = Open();
        if (device.AudioEndpointVolume.Mute || device.AudioEndpointVolume.MasterVolumeLevelScalar <= 0)
            throw new TerminalException("OUTPUT_MUTED", "教室扬声器静音或音量为零");
        synth.SelectVoice(voice.VoiceInfo.Name);
        using var wave = new MemoryStream(); synth.SetOutputToWaveStream(wave);
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        synth.SpeakCompleted += (_, e) => { if (e.Error is not null) completion.TrySetException(e.Error); else if (e.Cancelled) completion.TrySetCanceled(); else completion.TrySetResult(); };
        using var cancellation = token.Register(synth.SpeakAsyncCancelAll);
        synth.SpeakAsync(text); await completion.Task.WaitAsync(token);
        synth.SetOutputToNull(); wave.Position = 0;
        using var reader = new WaveFileReader(wave);
        using var output = new WasapiOut(device, AudioClientShareMode.Shared, false, 100);
        var finished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        output.PlaybackStopped += (_, e) => { if (e.Exception is not null) finished.TrySetException(e.Exception); else finished.TrySetResult(); };
        output.Init(reader); output.Play();
        using var stop = token.Register(output.Stop);
        await finished.Task.WaitAsync(token); token.ThrowIfCancellationRequested();
    }, token);
}
