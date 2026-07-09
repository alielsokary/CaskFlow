import AppKit
import UniformTypeIdentifiers
let args = CommandLine.arguments
let size = Int(args[4]) ?? 256
var icon: NSImage?
switch args[1] {
case "bundle":
    guard let bundle = Bundle(url: URL(fileURLWithPath: args[2])) else { exit(1) }
    let name = (bundle.infoDictionary?["CFBundleIconName"] as? String)
        ?? (bundle.infoDictionary?["CFBundleIconFile"] as? String) ?? "AppIcon"
    icon = bundle.image(forResource: name)
case "generic":
    icon = NSWorkspace.shared.icon(for: UTType.applicationBundle)
default:
    icon = NSWorkspace.shared.icon(forFile: args[2])
}
guard let resolved = icon else { exit(1) }
guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
resolved.size = NSSize(width: size, height: size)
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
resolved.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[3]))
