// Renders CaskHub's CLI terminal tile as a PNG, for CLI casks the app can't
// classify as CLI itself (a pkg artifact makes Cask.isCLI false, so without
// a served icon they'd show the generic window glyph — session-manager-plugin).
//
// CaskHub draws remote icons at 80% of the tile inside a well and applies its
// own clipShape (CaskIconView.swift), so the baked values compensate: glyph
// at 0.34/0.8 = 0.425 of the canvas, square corners (the app's clip owns the
// radius). Colors and font mirror CaskHub's tokens: ink 0x33304A, cream
// 0xF6E9CB, JetBrains Mono Bold.
import AppKit

// cli_tile.swift <font.ttf> <out.png> <size>
let args = CommandLine.arguments
guard args.count == 4, let size = Int(args[3]) else { exit(2) }

let fontURL = URL(fileURLWithPath: args[1]) as CFURL
guard CTFontManagerRegisterFontsForURL(fontURL, .process, nil),
      let font = NSFontManager.shared.font(
          withFamily: "JetBrains Mono", traits: .boldFontMask,
          weight: 9, size: CGFloat(size) * 0.425),
      font.familyName == "JetBrains Mono"
else { exit(1) }

func rgb(_ hex: Int) -> NSColor {
    NSColor(red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

rgb(0x33304A).setFill()
NSRect(x: 0, y: 0, width: size, height: size).fill()

let glyph = NSAttributedString(string: ">_", attributes: [
    .font: font, .foregroundColor: rgb(0xF6E9CB),
])
// Centering the typographic bounds matches SwiftUI's Text centering.
let bounds = glyph.size()
glyph.draw(at: NSPoint(x: (CGFloat(size) - bounds.width) / 2,
                       y: (CGFloat(size) - bounds.height) / 2))

NSGraphicsContext.current?.flushGraphics()
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[2]))
