package com.autocar.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.autocar.app.ui.screens.DashboardScreen
import com.autocar.app.ui.theme.Gold
import com.autocar.app.viewmodel.TerminalViewModel

private val configTabs = listOf("Dashboard", "Terminal")

@Composable
fun ConfigViewport(
    terminalVm: TerminalViewModel,
    selectedTab: Int = 0,
    onTabChange: (Int) -> Unit = {},
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxSize()) {
        TabRow(
            selectedTabIndex = selectedTab,
            containerColor = MaterialTheme.colorScheme.surface,
            contentColor = Gold,
            indicator = { tabPositions ->
                TabRowDefaults.SecondaryIndicator(
                    modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                    color = Gold,
                )
            },
        ) {
            configTabs.forEachIndexed { i, title ->
                Tab(
                    selected = selectedTab == i,
                    onClick = { onTabChange(i) },
                    text = { Text(title) },
                    selectedContentColor = Gold,
                    unselectedContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Box(Modifier.weight(1f)) {
            when (selectedTab) {
                0 -> DashboardScreen()
                1 -> TerminalViewport(terminalVm = terminalVm)
            }
        }
    }
}
