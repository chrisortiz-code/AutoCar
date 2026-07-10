package com.autocar.app.navigation

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Gamepad
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.autocar.app.data.gamepad.GamepadManager
import com.autocar.app.ui.components.FaceViewport
import com.autocar.app.ui.components.LidarPolarPlot
import com.autocar.app.ui.components.MjpegView
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
import com.autocar.app.viewmodel.SensorsViewModel
import com.autocar.app.viewmodel.SettingsViewModel

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
    val settingsVm: SettingsViewModel = viewModel()
    val layoutMode by settingsVm.layoutMode.collectAsState()

    Column(modifier = Modifier.fillMaxSize()) {
        TopBar()

        if (useRail) {
            if (layoutMode == "grid") {
                GridLayout(gamepadManager)
            } else {
                TabletLandscapeLayout(gamepadManager)
            }
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

// ── Grid layout: left rail + 2x2 grid with hold-to-enlarge ──

/** Identifies a cell in the 2x2 grid. */
private enum class GridCell { Camera, Depth, Lidar, Panel }

@Composable
private fun ColumnScope.GridLayout(gamepadManager: GamepadManager) {
    val sensorsVm: SensorsViewModel = viewModel()
    val sensorUpdate by sensorsVm.sensorUpdate.collectAsState()
    val cameraUrl by sensorsVm.cameraUrl.collectAsState()
    val depthUrl by sensorsVm.depthUrl.collectAsState()

    LaunchedEffect(Unit) { sensorsVm.connect() }

    var viewport by remember { mutableStateOf(Viewport.Sensors) }
    var expandedCell by remember { mutableStateOf<GridCell?>(null) }
    var panelTab by remember { mutableIntStateOf(0) }
    val panelTabs = listOf("Dashboard", "Drive", "Paths")

    Row(modifier = Modifier.weight(1f)) {
        // Left rail — viewport selector (same as classic)
        NavigationRail(
            containerColor = MaterialTheme.colorScheme.surface,
        ) {
            Viewport.entries.forEach { vp ->
                NavigationRailItem(
                    icon = { Icon(vp.icon, contentDescription = vp.label) },
                    label = { Text(vp.label) },
                    selected = viewport == vp && expandedCell == null,
                    onClick = {
                        viewport = vp
                        expandedCell = null
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

        // Main area — either expanded single cell or 2x2 grid
        if (expandedCell != null) {
            Box(modifier = Modifier.weight(1f).fillMaxSize()) {
                when (expandedCell) {
                    GridCell.Camera -> MjpegView(url = cameraUrl, modifier = Modifier.fillMaxSize())
                    GridCell.Depth -> MjpegView(url = depthUrl, modifier = Modifier.fillMaxSize())
                    GridCell.Lidar -> LidarPolarPlot(
                        points = sensorUpdate.lidar?.points ?: emptyList(),
                        modifier = Modifier.fillMaxSize(),
                    )
                    GridCell.Panel -> Column(Modifier.fillMaxSize()) {
                        TabRow(
                            selectedTabIndex = panelTab,
                            containerColor = MaterialTheme.colorScheme.surface,
                            contentColor = Gold,
                            indicator = { tabPositions ->
                                TabRowDefaults.SecondaryIndicator(
                                    modifier = Modifier.tabIndicatorOffset(tabPositions[panelTab]),
                                    color = Gold,
                                )
                            },
                        ) {
                            panelTabs.forEachIndexed { i, title ->
                                Tab(
                                    selected = panelTab == i,
                                    onClick = { panelTab = i },
                                    text = { Text(title) },
                                    selectedContentColor = Gold,
                                    unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                        Box(Modifier.weight(1f)) {
                            when (panelTab) {
                                0 -> DashboardScreen()
                                1 -> DriveScreen(gamepadManager = gamepadManager)
                                2 -> PathsScreen()
                            }
                        }
                    }
                    null -> {}
                }
                // Close button overlay
                IconButton(
                    onClick = { expandedCell = null },
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(8.dp)
                        .size(36.dp)
                        .background(
                            MaterialTheme.colorScheme.surface.copy(alpha = 0.7f),
                            CircleShape,
                        ),
                ) {
                    Icon(Icons.Default.Close, contentDescription = "Close", tint = Gold)
                }
            }
        } else when (viewport) {
            // Face / Track — full viewport like classic layout
            Viewport.Face -> Box(modifier = Modifier.weight(1f)) { FaceViewport() }
            Viewport.Track -> Box(modifier = Modifier.weight(1f)) { ObjectTrackViewport() }

            // Sensors — 2x2 grid with hold-to-enlarge
            Viewport.Sensors -> {
            // Two-column layout: cameras left, lidar + panel right
            Row(modifier = Modifier.weight(1f)) {
                // Left column — RGB + Depth stacked (wider to show feeds)
                Column(
                    modifier = Modifier
                        .weight(1.4f)
                        .fillMaxSize(),
                ) {
                    // Camera RGB
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { expandedCell = GridCell.Camera })
                            },
                    ) {
                        MjpegView(url = cameraUrl, modifier = Modifier.fillMaxSize())
                    }
                    // Depth
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { expandedCell = GridCell.Depth })
                            },
                    ) {
                        MjpegView(url = depthUrl, modifier = Modifier.fillMaxSize())
                    }
                }
                // Right column — Lidar + Panel
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxSize(),
                ) {
                    // Lidar (constrained to not overflow)
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .clipToBounds()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { expandedCell = GridCell.Lidar })
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        LidarPolarPlot(
                            points = sensorUpdate.lidar?.points ?: emptyList(),
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                    // Panel content (Dashboard/Drive/Paths)
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth()
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
                            .pointerInput(Unit) {
                                detectTapGestures(onLongPress = { expandedCell = GridCell.Panel })
                            },
                    ) {
                        Column(Modifier.fillMaxSize()) {
                            TabRow(
                                selectedTabIndex = panelTab,
                                containerColor = MaterialTheme.colorScheme.surface,
                                contentColor = Gold,
                                indicator = { tabPositions ->
                                    TabRowDefaults.SecondaryIndicator(
                                        modifier = Modifier.tabIndicatorOffset(tabPositions[panelTab]),
                                        color = Gold,
                                    )
                                },
                            ) {
                                panelTabs.forEachIndexed { i, title ->
                                    Tab(
                                        selected = panelTab == i,
                                        onClick = { panelTab = i },
                                        text = { Text(title) },
                                        selectedContentColor = Gold,
                                        unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            Box(Modifier.weight(1f)) {
                                when (panelTab) {
                                    0 -> DashboardScreen()
                                    1 -> DriveScreen(gamepadManager = gamepadManager)
                                    2 -> PathsScreen()
                                }
                            }
                        }
                    }
                }
            }
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
