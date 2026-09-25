using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Text;
using NAudio.CoreAudioApi;

namespace ClassroomMate.Windows;

public sealed record InputDevice(string Identifier, string Name, string NativeName);
public static class Devices
{
    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface ICreateDevEnum { [PreserveSig] int CreateClassEnumerator(ref Guid category, out IEnumMoniker? enumerator, int flags); }
    [ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyBag
    {
        [PreserveSig] int Read([MarshalAs(UnmanagedType.LPWStr)] string name, [MarshalAs(UnmanagedType.Struct)] out object value, IntPtr error);
        [PreserveSig] int Write([MarshalAs(UnmanagedType.LPWStr)] string name, [MarshalAs(UnmanagedType.Struct)] ref object value);
    }
    public static string StableId(string native) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(native)));
    public static InputDevice[] Cameras() => Enumerate(new("860BB310-5D01-11D0-BD3B-00A0C911CE86"));
    public static InputDevice[] Microphones() => Enumerate(new("33D9A762-90C8-11D0-BD43-00A0C911CE86"));
    static InputDevice[] Enumerate(Guid category)
    {
        var result = new List<InputDevice>();
        object? instance = null; IEnumMoniker? enumerator = null;
        try
        {
            instance = Activator.CreateInstance(Type.GetTypeFromCLSID(new("62BE5D10-60EB-11D0-BD3B-00A0C911CE86"))!);
            if (((ICreateDevEnum)instance!).CreateClassEnumerator(ref category, out enumerator, 0) != 0 || enumerator is null) return [];
            var monikers = new IMoniker[1];
            while (result.Count < 40 && enumerator.Next(1, monikers, IntPtr.Zero) == 0)
            {
                object? bag = null;
                try
                {
                    var iid = typeof(IPropertyBag).GUID;
                    monikers[0].BindToStorage(null!, null!, ref iid, out bag);
                    monikers[0].GetDisplayName(null!, null!, out var native);
                    ((IPropertyBag)bag).Read("FriendlyName", out var name, IntPtr.Zero);
                    var id = StableId(native);
                    // FFmpeg's dshow alternate device name replaces ':' with '_'.
                    result.Add(new(id, $"{name} [{id[..8]}]", native.Replace(':', '_')));
                }
                finally { if (bag is not null) Marshal.ReleaseComObject(bag); Marshal.ReleaseComObject(monikers[0]); }
            }
        }
        catch (COMException) { }
        finally { if (enumerator is not null) Marshal.ReleaseComObject(enumerator); if (instance is not null) Marshal.ReleaseComObject(instance); }
        return result.ToArray();
    }
    public static InputDevice[] Speakers()
    {
        try
        {
            using var enumerator = new MMDeviceEnumerator();
            return enumerator.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active).Select(d =>
            {
                using (d) { var id = StableId(d.ID); return new InputDevice(id, $"{d.FriendlyName} [{id[..8]}]", d.ID); }
            }).Take(40).ToArray();
        }
        catch (COMException) { return []; } // Keep display/control available if Windows Audio is unavailable.
    }
    public static string[] Screens() => Screen.AllScreens.Select(s => s.DeviceName).Take(20).ToArray();
}
