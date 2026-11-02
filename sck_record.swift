// sck_record.swift — tanıtım videosu aracı için ScreenCaptureKit ekran kaydedici.
//
// Neden ayrı ikili: bu makinedeki ffmpeg'in avfoundation girişi macOS 27'de "Capture
// screen" aygıtı LİSTELEMİYOR (yalnız kameralar) — ekran yakalamanın tek sağlam yolu SCK.
// Bonus: ilk karenin ana-makine saatini raporlar → tablet dokunuşlarıyla kare-doğru senkron.
//
// Derleme (promo.py otomatik yapar, kaynak değişince yeniden):
//   swiftc -O -parse-as-library -swift-version 5 -o .build/sck-record sck_record.swift
//
// Kullanım:
//   sck-record --list
//   sck-record --display <sıra|id:N|ad-parçası> --out ekran.mov [--fps 60] [--codec hevc|h264|prores]
//              [--mbps 60] [--no-cursor] [--seconds 30] [--max-width 3840]
// Çıktı: stdout'a JSON satırları — event: display | started | first_frame | stopped | error
// Durdurma: stdin'e "q" satırı ya da EOF, SIGINT/SIGTERM, ya da --seconds dolunca.
//
// İzin: "Ekran ve Sistem Sesi Kaydı" izni, komutu ÇALIŞTIRAN uygulamaya (Terminal/iTerm/IDE)
// verilir — bu ikiliye değil (macOS sorumlu süreci zincirin en üstü sayar).

import AppKit
import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let s = String(data: data, encoding: .utf8) {
        print(s)
        fflush(stdout)
    }
}

struct DisplayInfo {
    let id: CGDirectDisplayID
    let index: Int
    let name: String
    let main: Bool
    let builtin: Bool
    let points: (Int, Int)
    let pixels: (Int, Int)
    let hz: Double
    // üretici/model/seri kimliği: aynı adlı ekranları (ör. iki aynı monitör) ayırt etmek için JSON'a yazılır
    let vendor: UInt32
    let model: UInt32
    let serial: UInt32

    var json: [String: Any] {
        ["event": "display", "id": Int(id), "index": index, "name": name, "main": main, "builtin": builtin,
         "points": [points.0, points.1], "pixels": [pixels.0, pixels.1], "hz": hz,
         "vendor": Int(vendor), "model": Int(model), "serial": Int(serial)]
    }
}

func listDisplays() -> [DisplayInfo] {
    var count: UInt32 = 0
    CGGetActiveDisplayList(0, nil, &count)
    var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
    CGGetActiveDisplayList(count, &ids, &count)
    var names: [CGDirectDisplayID: String] = [:]
    for s in NSScreen.screens {
        if let n = s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber {
            names[CGDirectDisplayID(n.uint32Value)] = s.localizedName
        }
    }
    // Ana ekran önce (screencapture -D 1 ile aynı sıra)
    let ordered = ids.sorted { CGDisplayIsMain($0) != 0 && CGDisplayIsMain($1) == 0 }
    return ordered.enumerated().map { i, id in
        let mode = CGDisplayCopyDisplayMode(id)
        let b = CGDisplayBounds(id)
        return DisplayInfo(id: id, index: i + 1, name: names[id] ?? "Ekran \(id)",
                           main: CGDisplayIsMain(id) != 0, builtin: CGDisplayIsBuiltin(id) != 0,
                           points: (Int(b.width), Int(b.height)),
                           pixels: (mode?.pixelWidth ?? Int(b.width), mode?.pixelHeight ?? Int(b.height)),
                           hz: mode?.refreshRate ?? 0,
                           vendor: CGDisplayVendorNumber(id), model: CGDisplayModelNumber(id),
                           serial: CGDisplaySerialNumber(id))
    }
}

// TEK kural (promokit/capture.py resolve_display ile aynı): "N" = sıra no, "id:N" = CGDirectDisplayID,
// diğer = ad parçası. Sayı hem sıra hem kimlik olarak DENENMEZ — çakışınca `capture mac` ile
// `capture shot` farklı ekran seçiyordu.
func pickDisplay(_ arg: String, _ all: [DisplayInfo]) -> DisplayInfo? {
    let a = arg.trimmingCharacters(in: .whitespaces)
    if a.lowercased().hasPrefix("id:") {
        guard let n = UInt32(a.dropFirst(3)) else { return nil }
        return all.first { $0.id == n }
    }
    if let n = Int(a) {
        return (n >= 1 && n <= all.count) ? all[n - 1] : nil
    }
    let q = a.lowercased()
    return all.first { $0.name.lowercased().contains(q) }
}

final class Once: @unchecked Sendable {  // NSLock ile korunuyor
    private let lock = NSLock()
    private var cont: CheckedContinuation<Void, Never>?
    init(_ c: CheckedContinuation<Void, Never>) { cont = c }
    func fire() {
        lock.lock()
        let c = cont
        cont = nil
        lock.unlock()
        c?.resume()
    }
}

var keepAlive: [AnyObject] = []

func waitForStop(seconds: Double) async {
    await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
        let once = Once(cont)
        let q = DispatchQueue.global()
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: q)
            src.setEventHandler { once.fire() }
            src.resume()
            keepAlive.append(src)
        }
        let th = Thread {
            while let line = readLine() {
                if line.trimmingCharacters(in: .whitespaces).lowercased() == "q" { break }
            }
            once.fire()
        }
        th.start()
        if seconds > 0 {
            q.asyncAfter(deadline: .now() + seconds) { once.fire() }
        }
    }
}

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let writer: AVAssetWriter
    let input: AVAssetWriterInput
    let adaptor: AVAssetWriterInputPixelBufferAdaptor
    let queue = DispatchQueue(label: "promo.sck.rec")
    var started = false
    var stopping = false
    var firstPTS = CMTime.invalid
    var lastPTS = CMTime.invalid
    var frames = 0
    var dropped = 0

    init(url: URL, width: Int, height: Int, fps: Int, codec: String, bitrate: Int) throws {
        try? FileManager.default.removeItem(at: url)
        writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        var settings: [String: Any] = [
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoColorPropertiesKey: [
                AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
                AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
                AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
            ],
        ]
        switch codec {
        case "prores":
            settings[AVVideoCodecKey] = AVVideoCodecType.proRes422
        case "h264":
            settings[AVVideoCodecKey] = AVVideoCodecType.h264
            settings[AVVideoCompressionPropertiesKey] = [
                AVVideoAverageBitRateKey: bitrate, AVVideoExpectedSourceFrameRateKey: fps,
                AVVideoMaxKeyFrameIntervalKey: fps * 2,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            ]
        default:
            settings[AVVideoCodecKey] = AVVideoCodecType.hevc
            settings[AVVideoCompressionPropertiesKey] = [
                AVVideoAverageBitRateKey: bitrate, AVVideoExpectedSourceFrameRateKey: fps,
                AVVideoMaxKeyFrameIntervalKey: fps * 2,
            ]
        }
        input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil)
        guard writer.canAdd(input) else {
            throw NSError(domain: "sck-record", code: 1, userInfo: [NSLocalizedDescriptionKey: "AVAssetWriter girişi eklenemedi"])
        }
        writer.add(input)
        super.init()
        guard writer.startWriting() else {
            throw writer.error ?? NSError(domain: "sck-record", code: 2,
                                          userInfo: [NSLocalizedDescriptionKey: "AVAssetWriter başlatılamadı"])
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sb.isValid, !stopping else { return }
        guard let arr = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let raw = arr.first?[.status] as? Int,
              let status = SCFrameStatus(rawValue: raw), status == .complete,
              let pb = CMSampleBufferGetImageBuffer(sb) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sb)
        if !started {
            writer.startSession(atSourceTime: pts)
            started = true
            firstPTS = pts
            // Karenin ana-makine saatini duvar saatine çevir (dokunuş senkronunun çapası)
            let now = CMClockGetTime(CMClockGetHostTimeClock())
            let wall = Date().timeIntervalSince1970 - CMTimeGetSeconds(CMTimeSubtract(now, pts))
            emit(["event": "first_frame", "wall": wall,
                  "width": CVPixelBufferGetWidth(pb), "height": CVPixelBufferGetHeight(pb)])
        }
        if input.isReadyForMoreMediaData, adaptor.append(pb, withPresentationTime: pts) {
            frames += 1
        } else {
            dropped += 1
        }
        lastPTS = pts
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        emit(["event": "error", "message": "akış durdu: \(error.localizedDescription)"])
    }

    func finish() async {
        queue.sync { stopping = true }
        input.markAsFinished()
        if started {
            await writer.finishWriting()
        } else {
            writer.cancelWriting()
        }
        let dur = started ? CMTimeGetSeconds(CMTimeSubtract(lastPTS, firstPTS)) : 0
        // status: AVAssetWriter.Status (2 = completed). Çağıran yalnız 2'yi başarı sayar — yazıcı yarıda
        // düşerse append'ler sessizce "atlanan" kare olur, dosya bozuk kalır
        var out: [String: Any] = ["event": "stopped", "frames": frames, "dropped": dropped, "duration": dur,
                                  "status": writer.status.rawValue]
        if let e = writer.error { out["writer_error"] = e.localizedDescription }
        emit(out)
    }
}

@main
struct SCKRecord {
    static func main() async {
        var args = Array(CommandLine.arguments.dropFirst())
        func take(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            let v = args[i + 1]
            args.removeSubrange(i...(i + 1))
            return v
        }
        func flag(_ name: String) -> Bool {
            guard let i = args.firstIndex(of: name) else { return false }
            args.remove(at: i)
            return true
        }
        let displays = listDisplays()
        if flag("--list") {
            for d in displays { emit(d.json) }
            exit(0)
        }
        let dispArg = take("--display") ?? "1"
        guard let outPath = take("--out") else {
            emit(["event": "error", "message": "--out gerekli"])
            exit(64)
        }
        let fps = Int(take("--fps") ?? "60") ?? 60
        let codec = take("--codec") ?? "hevc"
        let seconds = Double(take("--seconds") ?? "0") ?? 0
        let maxWidth = Int(take("--max-width") ?? "0") ?? 0
        let cursor = !flag("--no-cursor")
        guard let info = pickDisplay(dispArg, displays) else {
            emit(["event": "error", "message": "ekran bulunamadı: \(dispArg)"])
            exit(65)
        }
        var (w, h) = info.pixels
        if maxWidth > 0 && w > maxWidth {
            h = Int((Double(h) * Double(maxWidth) / Double(w)).rounded()) / 2 * 2
            w = maxWidth
        }
        let mbps = Double(take("--mbps") ?? "") ?? min(120, max(20, Double(w * h * fps) * 0.20 / 1e6))
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first(where: { $0.displayID == info.id }) else {
                emit(["event": "error", "message": "SCK ekranı göremedi: \(info.name)"])
                exit(66)
            }
            let cfg = SCStreamConfiguration()
            cfg.width = w
            cfg.height = h
            cfg.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
            cfg.queueDepth = 8
            cfg.showsCursor = cursor
            cfg.pixelFormat = kCVPixelFormatType_32BGRA
            cfg.colorSpaceName = CGColorSpace.sRGB
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let rec = try Recorder(url: URL(fileURLWithPath: outPath), width: w, height: h, fps: fps,
                                   codec: codec, bitrate: Int(mbps * 1e6))
            let stream = SCStream(filter: filter, configuration: cfg, delegate: rec)
            try stream.addStreamOutput(rec, type: .screen, sampleHandlerQueue: rec.queue)
            try await stream.startCapture()
            emit(["event": "started", "id": Int(info.id), "name": info.name, "width": w, "height": h,
                  "fps": fps, "codec": codec, "mbps": mbps])
            await waitForStop(seconds: seconds)
            try? await stream.stopCapture()
            await rec.finish()
            exit(0)
        } catch {
            let ns = error as NSError
            emit(["event": "error", "message": ns.localizedDescription, "domain": ns.domain, "code": ns.code])
            exit(2)
        }
    }
}
