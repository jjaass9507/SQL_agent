# Google Stitch 設計系統：Pro Space Gray（權威設計參考）

> 來源：使用者的 Stitch 專案「SQL Agent Platform」（projects/6954640829770779128）
> 的 DESIGN.md，經 Stitch MCP 取回、原文收錄。
> 用途：Phase 8 設計置換的權威規格——tokens.css 與元件樣式一律以本文件為準
> （整合工序見 docs/v2_rebuild_plan.md 9-2）。

---

---
name: Pro Space Gray
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1b1b1c'
  surface-container: '#202020'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353535'
  on-surface: '#e5e2e1'
  on-surface-variant: '#c0c6d6'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#303030'
  outline: '#8b91a0'
  outline-variant: '#414754'
  surface-tint: '#aac7ff'
  primary: '#aac7ff'
  on-primary: '#003064'
  primary-container: '#3e90ff'
  on-primary-container: '#002957'
  inverse-primary: '#005db8'
  secondary: '#c8c6c5'
  on-secondary: '#303030'
  secondary-container: '#474746'
  on-secondary-container: '#b7b5b4'
  tertiary: '#c8c6c5'
  on-tertiary: '#313030'
  tertiary-container: '#929090'
  on-tertiary-container: '#2a2a2a'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d6e3ff'
  primary-fixed-dim: '#aac7ff'
  on-primary-fixed: '#001b3e'
  on-primary-fixed-variant: '#00468d'
  secondary-fixed: '#e4e2e1'
  secondary-fixed-dim: '#c8c6c5'
  on-secondary-fixed: '#1b1c1c'
  on-secondary-fixed-variant: '#474746'
  tertiary-fixed: '#e5e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1c1b1b'
  on-tertiary-fixed-variant: '#474746'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353535'
  bg-base: '#1e1e1e'
  bg-sidebar: '#161616'
  bg-panel: '#252525'
  bg-active: '#2d2d2d'
  border-hairline: '#323232'
  status-success: '#30d158'
  status-error: '#ff453a'
  status-warning: '#ff9f0a'
  text-primary: '#ffffff'
  text-secondary: '#ebebf599'
  text-tertiary: '#ebebf54d'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '450'
    lineHeight: 18px
  mono-data:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 1px
  margin-sm: 8px
  margin-md: 16px
  panel-padding: 12px
---

## Brand & Style

The design system is a high-utility, dark-mode first interface inspired by Apple's Pro Applications (Xcode, Instruments). It prioritizes **Technical Precision**, **Structural Depth**, and **Absolute Utility** over decorative flourishes. 

The aesthetic is characterized by a "No-Slop" philosophy: 
- **Clarity:** The UI recedes to prioritize content—SQL queries, data schemas, and execution traces.
- **Minimalism & Deference:** Avoidance of gradients, glowing effects, and unnecessary animations.
- **Structural Depth:** Depth is communicated through layered background values and hairline borders rather than drop shadows.
- **Professional Tone:** The system evokes the feeling of a quiet, masterful systems engineer—efficient, direct, and authoritative.

The interface follows a strict utility-first approach, ensuring that every element serves a functional purpose in a complex data environment.

## Colors

The palette is rooted in the **macOS Pro Space Gray** ecosystem. It is a monochromatic-first system with high-vibrancy semantic accents.

- **Backgrounds:** Use a tiered gray system to define hierarchy. `bg-sidebar` is the deepest tier, `bg-base` serves as the primary workspace, and `bg-panel` is used for elevated editor or inspector areas.
- **Borders:** Hairline borders (#323232) are the primary method of separation. Avoid using gaps or shadows for layout containment.
- **Typography Colors:** Adhere to Apple's hierarchy of vibrancy. Primary text is pure white, secondary text uses 60% opacity for subheadings, and tertiary text uses 30% for metadata and placeholders.
- **Semantic Accents:** Use system-standard colors for status. Blue for primary actions/queries, Green for success/index hits, Red for syntax/connection errors, and Orange for performance warnings or full scans.

## Typography

The system uses a combination of high-performance sans-serif and monospaced fonts to create an IDE-like atmosphere.

- **General UI:** Uses a system-centric sans-serif (Inter) at a compact 13px base size. This ensures high information density without sacrificing legibility.
- **Monospace & Code:** All SQL input, AST traces, and data table values must use a monospaced font (JetBrains Mono/SF Mono). 
- **Data Tables:** Numbers in tables must be set in monospace and right-aligned to allow for easy visual comparison of magnitudes.
- **Syntax Highlighting:** Highlighting should be calm. Avoid "neon" palettes. Use Blue (#0a84ff) for keywords, Green (#30d158) for strings, and Orange (#ff9f0a) for numeric literals.

## Layout & Spacing

The layout follows a **Fixed-Grid Pro App Architecture** consisting of a three-column shell.

- **Column 1 (Sidebar):** 240px - 280px. Schema explorer and navigation.
- **Column 2 (Main):** Fluid. SQL Editor and Results table.
- **Column 3 (Inspector):** 300px. Thinking traces, performance metadata, and AI logs.

**Key Layout Rules:**
- **Hairline Dividers:** Sections are separated by 1px solid borders (`--color-border`). Do not use large margins or gutters to create separation.
- **Independent Scrolling:** Each column maintains its own scroll state using slim, macOS-style scrollbars.
- **Density:** Padding is kept tight (8px to 12px) to maximize the visible data rows and code lines.
- **Alignment:** Labels are generally sentence-case and left-aligned. Numeric data in results tables must be right-aligned.

## Elevation & Depth

This system rejects the use of ambient shadows in favor of **Tonal Layers** and **Crisp Outlines**.

- **Layering:** Hierarchy is established by background color. Sidebars are the darkest (`#161616`), the main canvas is the middle tier (`#1e1e1e`), and active editors or modals are the lightest (`#252525`).
- **Hairline Borders:** Every functional area is enclosed in a 1px border. This mimics the physical "machined" look of professional software.
- **Active States:** Selection is indicated through background color shifts (`#2d2d2d`) or a solid System Blue tint for primary selections.
- **No Glow:** No outer glows or drop shadows are permitted, even for active buttons or "AI" features.

## Shapes

The design system uses a **Soft** shape language to provide a slight tactile feel while maintaining a professional, structured appearance.

- **Standard Elements:** Buttons, input fields, and select menus use a 6px (`rounded-sm`) radius.
- **Small Elements:** Pills and status badges use a 4px (`rounded-xs`) radius.
- **Large Elements:** Main content panels and modals use a 10px or 12px (`rounded-md/lg`) radius to soften the outer frame of the application.
- **Squircle Logic:** Where possible, use smooth corner smoothing (Apple's squircle) to ensure the interface feels premium and integrated with the OS.

## Components

### Buttons
- **Primary:** Solid System Blue background with white text. No gradient.
- **Secondary:** Ghost style with a 1px border (#323232) and no background until hover.
- **Interactions:** Use a spring transition (`cubic-bezier(0.16, 1, 0.3, 1)`) for hover and active states.

### SQL Editor
- **Environment:** Background should be `bg-panel` (#252525) to distinguish the active workspace from the chrome.
- **Line Numbers:** Right-aligned, tertiary text color, monospaced.

### Data Tables
- **Header:** 1px border-bottom, secondary text color, bold.
- **Rows:** Alternating background "zebra striping" is discouraged. Use hairline borders between rows or a subtle hover effect on the entire row.

### Status Indicators
- **Dots:** Use 8px solid colored circles (`--accent-blue`, `--accent-green`, etc.) for status logs instead of icons or emojis.
- **Warnings:** Use a small 1px bordered badge with an orange tint for performance alerts like "Full Table Scan."

### Input Fields
- **Default:** Transparent background with a 1px border.
- **Focus:** 1px border shifts to System Blue (`#0a84ff`) with no outer glow.

### Sidebar Items
- **Hover:** Rounded rectangle background (`bg-panel`).
- **Selection:** Solid background (`bg-active`) with bright white text. Use SF Symbol-style monochrome icons only.