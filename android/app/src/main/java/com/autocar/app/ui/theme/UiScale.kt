package com.autocar.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.unit.sp

/** Current UI scale factor (1.0 = normal, up to 2.0). */
val LocalUiScale = compositionLocalOf { 1.0f }

/** Scale levels: index → factor. */
val ScaleLevels = listOf(1.0f, 1.25f, 1.5f, 1.75f, 2.0f)

/** Returns a copy of [base] with all font sizes multiplied by [factor]. */
fun scaledTypography(base: Typography, factor: Float): Typography {
    if (factor == 1.0f) return base
    return Typography(
        displayLarge = base.displayLarge.copy(fontSize = (base.displayLarge.fontSize.value * factor).sp),
        displayMedium = base.displayMedium.copy(fontSize = (base.displayMedium.fontSize.value * factor).sp),
        displaySmall = base.displaySmall.copy(fontSize = (base.displaySmall.fontSize.value * factor).sp),
        headlineLarge = base.headlineLarge.copy(fontSize = (base.headlineLarge.fontSize.value * factor).sp),
        headlineMedium = base.headlineMedium.copy(fontSize = (base.headlineMedium.fontSize.value * factor).sp),
        headlineSmall = base.headlineSmall.copy(fontSize = (base.headlineSmall.fontSize.value * factor).sp),
        titleLarge = base.titleLarge.copy(fontSize = (base.titleLarge.fontSize.value * factor).sp),
        titleMedium = base.titleMedium.copy(fontSize = (base.titleMedium.fontSize.value * factor).sp),
        titleSmall = base.titleSmall.copy(fontSize = (base.titleSmall.fontSize.value * factor).sp),
        bodyLarge = base.bodyLarge.copy(fontSize = (base.bodyLarge.fontSize.value * factor).sp),
        bodyMedium = base.bodyMedium.copy(fontSize = (base.bodyMedium.fontSize.value * factor).sp),
        bodySmall = base.bodySmall.copy(fontSize = (base.bodySmall.fontSize.value * factor).sp),
        labelLarge = base.labelLarge.copy(fontSize = (base.labelLarge.fontSize.value * factor).sp),
        labelMedium = base.labelMedium.copy(fontSize = (base.labelMedium.fontSize.value * factor).sp),
        labelSmall = base.labelSmall.copy(fontSize = (base.labelSmall.fontSize.value * factor).sp),
    )
}
