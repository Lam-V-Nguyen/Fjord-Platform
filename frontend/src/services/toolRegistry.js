// Danh mục các công cụ (widget) của nền tảng.
// Thay thế chuỗi if/else trong mainManager cũ và menu hover (mainMenu.html):
// mỗi mục khai báo id, tiêu đề, file HTML (iframe), kích thước widget trên Gridstack và icon.
// Sidebar mới được sinh tự động từ danh mục này.

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
            { id: 'preparation-hyd', title: 'Data Preparation for HYD Scenario', short: 'Data Preparation', label: 'Data Preparation (HYD)', url: '/src_frontend/htmls/hydPreparation.html', w: 12, h: 10, icon: 'clipboard' },
            { id: 'grid-generation', title: 'Grid Generation', url: '/src_frontend/htmls/unstructuredGrid.html', w: 12, h: 10, icon: 'grid' },
            { id: 'new-hyd', title: 'Create HYD Scenario', url: '/src_frontend/htmls/hydCreator.html', w: 11, h: 9, icon: 'plus' },
            { id: 'run-hyd', title: 'Run HYD Scenario', url: '/src_frontend/htmls/hydRunner.html', w: 11, h: 3, icon: 'play' },
        ],
    },
    {
        id: 'waq', title: 'Water Quality (WAQ)', icon: 'flask',
        tools: [
            { id: 'new-waq', title: 'Create WAQ Scenario', url: '/src_frontend/htmls/waqCreator.html', w: 11, h: 9, icon: 'plus' },
            { id: 'run-waq', title: 'Run WAQ Scenario', url: '/src_frontend/htmls/waqRunner.html', w: 11, h: 3, icon: 'play' },
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
    {
        id: 'visualization', title: 'Visualization', icon: 'chart',
        tools: [
            { id: 'visualization', title: 'Visualization', url: '/src_frontend/htmls/visualization.html', w: 12, h: 9, icon: 'chart' },
        ],
    },
    {
        id: 'calibration', title: 'Model Calibration', icon: 'sliders',
        tools: [
            { id: 'calibration', title: 'Model Calibration', url: '/src_frontend/htmls/calibration.html', w: 12, h: 11, icon: 'sliders' },
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

// Nhóm chứa công cụ (để lấy icon/tên nhóm khi công cụ đứng riêng, vd. nút nhanh)
export function groupOf(id) {
    return TOOL_GROUPS.find(g => g.tools.some(t => t.id === id)) || null;
}

// Nhãn hiển thị khi công cụ đứng ngoài nhóm của nó:
// 'label' nếu có, nếu không thì 'short' kèm tên nhóm khi trùng với công cụ khác, cuối cùng là 'title'.
export function standaloneLabel(tool) {
    if (tool.label) return tool.label;
    const short = tool.short || tool.title;
    const duplicates = ALL.filter(t => (t.short || t.title) === short).length > 1;
    const g = groupOf(tool.id);
    return duplicates && g ? `${short} (${g.title})` : short;
}

// Nút nhanh trên Workspace trống, theo thứ tự quy trình mô hình
export const QUICK_TOOLS = ['data-download', 'preparation-hyd', 'grid-generation', 'new-hyd', 'new-waq', 'flow-preparation', 'visualization'];