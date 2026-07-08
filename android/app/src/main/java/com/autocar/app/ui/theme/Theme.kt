package com.autocar.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val AutoCarColorScheme = darkColorScheme(
    primary = Gold,
    onPrimary = SurfaceDark,
    primaryContainer = GoldDark,
    onPrimaryContainer = GoldLight,
    secondary = GoldMuted,
    onSecondary = SurfaceDark,
    secondaryContainer = GoldDark,
    onSecondaryContainer = GoldLight,
    tertiary = GoldLight,
    onTertiary = SurfaceDark,
    background = Black,
    onBackground = OnDarkPrimary,
    surface = SurfaceDark,
    onSurface = OnDarkPrimary,
    surfaceVariant = SurfaceVariantDark,
    onSurfaceVariant = OnDarkSecondary,
    // Unify all container surfaces so Cards, NavBar, etc. match
    surfaceContainer = SurfaceVariantDark,
    surfaceContainerLow = SurfaceVariantDark,
    surfaceContainerHigh = SurfaceVariantDark,
    surfaceContainerHighest = SurfaceVariantDark,
    surfaceContainerLowest = SurfaceDark,
    surfaceBright = SurfaceVariantDark,
    surfaceDim = SurfaceDark,
    outline = GoldMuted,
    outlineVariant = GoldDark,
    inverseSurface = GoldLight,
    inverseOnSurface = SurfaceDark,
    inversePrimary = GoldDark,
)

@Composable
fun AutoCarTheme(
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = AutoCarColorScheme,
        typography = Typography,
        content = content,
    )
}
