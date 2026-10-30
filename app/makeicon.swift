import AppKit
import Foundation

// HanFix 앱 아이콘 — 스퀴클 + 그라디언트 + '한'
let size: CGFloat = 1024
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()

let rect = NSRect(x: 0, y: 0, width: size, height: size)
NSColor.clear.setFill()
rect.fill()

// macOS 아이콘 그리드: 콘텐츠는 캔버스의 약 80%, 모서리 반경 ~22.4%
let inset: CGFloat = size * 0.098
let body = rect.insetBy(dx: inset, dy: inset)
let radius = body.width * 0.2237
let path = NSBezierPath(roundedRect: body, xRadius: radius, yRadius: radius)

let gradient = NSGradient(colors: [
    NSColor(srgbRed: 0.16, green: 0.47, blue: 0.88, alpha: 1),
    NSColor(srgbRed: 0.05, green: 0.22, blue: 0.55, alpha: 1),
])!
gradient.draw(in: path, angle: -70)

// 상단 하이라이트
let hl = NSBezierPath(roundedRect: body.insetBy(dx: body.width * 0.012, dy: body.width * 0.012),
                      xRadius: radius * 0.97, yRadius: radius * 0.97)
NSColor.white.withAlphaComponent(0.10).setStroke()
hl.lineWidth = size * 0.008
hl.stroke()

// 글자 '한'
let text = "한"
let style = NSMutableParagraphStyle()
style.alignment = .center
let font = NSFont(name: "AppleSDGothicNeo-Bold", size: size * 0.44)
    ?? NSFont.systemFont(ofSize: size * 0.44, weight: .bold)
let attrs: [NSAttributedString.Key: Any] = [
    .font: font,
    .foregroundColor: NSColor.white,
    .paragraphStyle: style,
]
let str = NSAttributedString(string: text, attributes: attrs)
let bounds = str.boundingRect(with: NSSize(width: size, height: size),
                              options: [.usesLineFragmentOrigin])
// 한글은 시각적 무게가 왼쪽에 쏠리므로 아주 살짝 오른쪽으로 보정
let origin = NSPoint(x: (size - bounds.width) / 2 + size * 0.012,
                     y: (size - bounds.height) / 2 - size * 0.005)
str.draw(at: origin)

img.unlockFocus()

guard let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    print("icon render failed"); exit(1)
}
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/icon_1024.png"
try png.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
