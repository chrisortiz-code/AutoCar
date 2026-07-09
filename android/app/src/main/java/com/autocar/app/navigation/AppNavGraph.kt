package com.autocar.app.navigation

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Gamepad
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.components.FaceViewport
import com.autocar.app.ui.components.ObjectTrackViewport
import com.autocar.app.ui.components.SensorViewport
import com.autocar.app.ui.components.SidePanel
import com.autocar.app.ui.screens.DashboardScreen
import com.autocar.app.ui.screens.DriveScreen
import com.autocar.app.ui.screens.PathsScreen
import com.autocar.app.ui.theme.Gold
import com.autocar.app.ui.theme.GoldDark
import com.autocar.app.ui.theme.Rajdhani
import com.autocar.app.ui.theme.SurfaceDark

enum class NavTab(val label: String, val icon: ImageVector) {
    Sensors("Sensors", Icons.Default.Sensors),
    Face("Face", Icons.Default.Face),
    Track("Track", Icons.Default.CenterFocusStrong),
    Dashboard("Dashboard", Icons.Default.Dashboard),
    Drive("Drive", Icons.Default.Gamepad),
    Paths("Paths", Icons.Default.Route),
}

/** Viewport tabs shown in the left rail (tablet landscape). */
private enum class Viewport(val label: String, val icon: ImageVector) {
    Sensors("Sensors", Icons.Default.Sensors),
    Face("Face", Icons.Default.Face),
    Track("Track", Icons.Default.CenterFocusStrong),
}

/** Tabs shown in the right rail (tablet landscape) — side-panel content. */
private val railTabs = listOf(NavTab.Dashboard, NavTab.Drive, NavTab.Paths)

@Composable
fun AppNavGraph(gamepadManager: GamepadManager) {
    val config = LocalConfiguration.current
    val useRail = config.screenWidthDp >= 600 &&
        config.orientation == Configuration.ORIENTATION_LANDSCAPE

    Column(modifier = Modifier.fillMaxSize()) {
        TopBar()

        if (useRail) {
            TabletLandscapeLayout(gamepadManager)
        } else {
            PhoneLayout(gamepadManager)
        }
    }
}

@Composable
private fun TopBar() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(44.dp)
            .background(
                Brush.horizontalGradient(
                    colors = listOf(GoldDark, Gold, GoldDark),
                )
            ),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(
            text = "AUTOCAR",
            fontFamily = Rajdhani,
            fontWeight = FontWeight.Bold,
            fontSize = 22.sp,
            color = SurfaceDark,
            letterSpacing = 3.sp,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
    }
}

// ── Tablet landscape: left rail (viewport) + viewport + side panel + right rail ──

@Composable
private fun ColumnScope.TabletLandscapeLayout(gamepadManager: GamepadManager) {
    var activeTab by remember { mutableStateOf<NavTab?>(null) }
    var viewport by remember { mutableStateOf(Viewport.Sensors) }

    Row(modifier = Modifier.weight(1f)) {
        // Left rail — viewport selector
        NavigationRail(
            containerColor = MaterialTheme.colorScheme.surface,
        ) {
            Viewport.entries.forEach { vp ->
                NavigationRailItem(
                    icon = { Icon(vp.icon, contentDescription = vp.label) },
                    label = { Text(vp.label) },
                    selected = viewport == vp,
                    onClick = { viewport = vp },
                    colors = NavigationRailItemDefaults.colors(
                        selectedIconColor = Gold,
                        selectedTextColor = Gold,
                        indicatorColor = GoldDark.copy(alpha = 0.25f),
                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
            }
        }

        // Main viewport
        Box(modifier = Modifier.weight(1f)) {
            when (viewport) {
                Viewport.Sensors -> SensorViewport()
                Viewport.Face -> FaceViewport()
                Viewport.Track -> ObjectTrackViewport()
            }
        }

        // Side panel
        SidePanel(
            visible = activeTab != null,
            title = activeTab?.label ?: "",
            onClose = { activeTab = null },
        ) {
            when (activeTab) {
                NavTab.Dashboard -> DashboardScreen()
                NavTab.Drive -> DriveScreen(gamepadManager = gamepadManager)
                NavTab.Paths -> PathsScreen()
                else -> {}
            }
        }

        // Right rail — side panel tabs
        NavigationRail(
            containerColor = MaterialTheme.colorScheme.surface,
        ) {
            railTabs.forEach { tab ->
                NavigationRailItem(
                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                    label = { Text(tab.label) },
                    selected = activeTab == tab,
                    onClick = {
                        activeTab = if (activeTab == tab) null else tab
                    },
                    colors = NavigationRailItemDefaults.colors(
                        selectedIconColor = Gold,
                        selectedTextColor = Gold,
                        indicatorColor = GoldDark.copy(alpha = 0.25f),
                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )
            }
        }
    }
}

// ── Phone / tablet portrait: bottom nav with full-screen tabs ──

@Composable
private fun ColumnScope.PhoneLayout(gamepadManager: GamepadManager) {
    var selectedTab by remember { mutableStateOf(NavTab.Sensors) }

    Box(modifier = Modifier.weight(1f)) {
        when (selectedTab) {
            NavTab.Sensors -> SensorViewport()
            NavTab.Face -> FaceViewport()
            NavTab.Track -> ObjectTrackViewport()
            NavTab.Dashboard -> DashboardScreen()
            NavTab.Drive -> DriveScreen(gamepadManager = gamepadManager)
            NavTab.Paths -> PathsScreen()
        }
    }

    NavigationBar(
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        NavTab.entries.forEach { tab ->
            NavigationBarItem(
                icon = { Icon(tab.icon, contentDescription = tab.label) },
                label = { Text(tab.label) },
                selected = selectedTab == tab,
                onClick = { selectedTab = tab },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = Gold,
                    selectedTextColor = Gold,
                    indicatorColor = GoldDark.copy(alpha = 0.25f),
                    unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
            )
        }
    }
}
