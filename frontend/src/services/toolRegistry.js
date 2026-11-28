// Catalogue of the platform's tools (widgets).
// Replaces the if/else chain in the old mainManager and the hover menu (mainMenu.html):
// each entry declares the id, title, HTML file (iframe), Gridstack widget size and icon.
// The new sidebar is generated automatically from this catalogue.

export const TOOL_GROUPS = [
    {
        id: 'data', title: 'Data Download', icon: 'download',
        tools: [
            { id: 'data-download', title: 'Data Download', url: '/src_frontend/htmls/dataDownload.html', w: 12, h: 12, icon: 'download' },
        ],
    },
    {
        id: 'hyd', title: 'Hydrodynamics (HYD)', icon: 'waves',
        tools: [
            { id: 'grid-generation', title: 'Grid Generation', url: '/src_frontend/htmls/unstructuredGrid.html', w: 12, h: 10, icon: 'grid' },
            { id: 'preparation-hyd', title: 'Data Preparation for HYD Scenario', short: 'Data Preparation', label: 'Data Preparation (HYD)', url: '/src_frontend/htmls/hydPreparation.html', w: 12, h: 10, icon: 'clipboard' },
            { id: 'new-hyd', title: 'Create HYD Scenario', url: '/src_frontend/htmls/hydCreator.html', w: 12, h: 9, icon: 'plus' },
            { id: 'run-hyd', title: 'Run HYD Scenario', url: '/src_frontend/htmls/hydRunner.html', w: 12, h: 3, icon: 'play' },
        ],
    },
    {
        id: 'calibration', title: 'Model Calibration', icon: 'sliders',
        tools: [
            { id: 'calibration', title: 'Model Calibration', url: '/src_frontend/htmls/calibration.html', w: 12, h: 11, icon: 'sliders' },
        ],
    },
    {
        id: 'waq', title: 'Water Quality (WAQ)', icon: 'flask',
        tools: [
            { id: 'new-waq', title: 'Create WAQ Scenario', url: '/src_frontend/htmls/waqCreator.html', w: 12, h: 9, icon: 'plus' },
            { id: 'run-waq', title: 'Run WAQ Scenario', url: '/src_frontend/htmls/waqRunner.html', w: 12, h: 3, icon: 'play' },
        ],
    },
    {
        id: 'visualization', title: 'Visualization', icon: 'chart',
        tools: [
            { id: 'visualization', title: 'Visualization', url: '/src_frontend/htmls/visualization.html', w: 12, h: 9, icon: 'chart' },
        ],
    },
    {
        id: 'flow', title: 'Flow Estimation', icon: 'river',
        tools: [
            { id: 'flow-preparation', title: 'Data Preparation for Flow Estimation', short: 'Data Preparation', label: 'Data Preparation (Wflow)', url: '/src_frontend/htmls/flowPreparation.html', w: 12, h: 11, icon: 'clipboard' },
            { id: 'run-flow-model', title: 'Run Flow Model', url: '/src_frontend/htmls/flowModel.html', w: 12, h: 9, icon: 'play' },
            { id: 'flow-estimation', title: 'Flow Estimation', url: '/src_frontend/htmls/flowRunner.html', w: 12, h: 12, icon: 'activity' },
        ],
    },

];

export const HELP_ITEMS = [
    { id: 'help-docs', title: 'User Manual', type: 'pdf', url: 'QuickManual.pdf', icon: 'book' },
    { id: 'about', title: 'About', url: '/src_frontend/htmls/about.html', w: 8, h: 5, icon: 'info' },
];

const ALL = [...TOOL_GROUPS.flatMap(g => g.tools), ...HELP_ITEMS];

export function getTool(id) {
    return ALL.find(t => t.id === id) || null;
}

export function allTools() { return ALL.slice(); }

// Group containing the tool (for the group icon/name when the tool stands alone, e.g. quick buttons)
export function groupOf(id) {
    return TOOL_GROUPS.find(g => g.tools.some(t => t.id === id)) || null;
}

// Label shown when a tool appears outside its group:
// 'label' if present, else 'short' plus the group name when it clashes with another tool, finally 'title'.
export function standaloneLabel(tool) {
    if (tool.label) return tool.label;
    const short = tool.short || tool.title;
    const duplicates = ALL.filter(t => (t.short || t.title) === short).length > 1;
    const g = groupOf(tool.id);
    return duplicates && g ? `${short} (${g.title})` : short;
}

// Quick buttons on the empty Workspace, in modelling-workflow order
export const QUICK_TOOLS = ['data-download', 'preparation-hyd', 'grid-generation', 'new-hyd', 'new-waq', 'flow-preparation', 'visualization'];