window.RelaxZoneManual = [
  {
    id: 'map',
    title: 'Explore the NISAR image',
    body: 'Drag the map to move around. Open Acquisitions to switch dates or radar channels. The gray image shows how much radar energy bounced back. Open Source audit to download the measured data as a GeoTIFF.',
    links: [{label: 'NISAR GCOV product guide', url: 'https://nisar-docs.asf.alaska.edu/gcov/'}],
  },
  {
    id: 'channels',
    title: 'Choose a polarization',
    body: 'The first letter identifies the transmitted polarization and the second identifies the received polarization. HH and VV are co-polarized. HV and VH are cross-polarized. The matrix shows the channels available for each acquisition and frequency. Compare images using the same channel, track, and processing when possible.',
    links: [{label: 'NISAR GCOV covariance layers', url: 'https://nisar-docs.asf.alaska.edu/gcov/'}],
  },
  {
    id: 'catalogue',
    title: 'Find more NISAR data',
    body: 'Search by study area and date. New catalogue entries show the provider footprint and available browse images. To request calibrated pixels for a supported GCOV channel, prepare a subset and follow the NASA Earthdata link in your signed-in browser.',
    links: [{label: 'ASF catalogue', url: 'https://search.asf.alaska.edu/'}, {label: 'NISAR Harmony subsets', url: 'https://nisar-docs.asf.alaska.edu/harmony/'}],
  },
  {
    id: 'assistant',
    title: 'Ask the Assistant',
    body: 'Try “Show me wetlands” or “Show me oil” to open a site and see what to look for. You can ask “What can I compare at Curuai?”, “Which channel should I use?” or “Could that dark patch be oil?” The assistant links to the readings and studies behind its answers.',
    links: [{label: 'How these answers are checked', url: '/science-review'}],
  },
  {
    id: 'interpretation',
    title: 'Read the map highlights',
    body: 'Red identifies oil study areas or review markers. Orange identifies wetland study areas. The grayscale radar layer shows relative backscatter. A dark feature can have several physical explanations. Site outlines and review points identify where to investigate.',
    links: [{label: 'NISAR data review', url: '/science-review'}],
  },
];
