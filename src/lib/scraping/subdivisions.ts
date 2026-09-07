export type CountrySubdivision = {
  code: string;
  name: string;
};

export const COUNTRY_SUBDIVISIONS: Record<string, CountrySubdivision[]> = {
  // United States - 50 states + DC + Puerto Rico
  US: [
    { code: "AL", name: "Alabama" },
    { code: "AK", name: "Alaska" },
    { code: "AZ", name: "Arizona" },
    { code: "AR", name: "Arkansas" },
    { code: "CA", name: "California" },
    { code: "CO", name: "Colorado" },
    { code: "CT", name: "Connecticut" },
    { code: "DE", name: "Delaware" },
    { code: "FL", name: "Florida" },
    { code: "GA", name: "Georgia" },
    { code: "HI", name: "Hawaii" },
    { code: "ID", name: "Idaho" },
    { code: "IL", name: "Illinois" },
    { code: "IN", name: "Indiana" },
    { code: "IA", name: "Iowa" },
    { code: "KS", name: "Kansas" },
    { code: "KY", name: "Kentucky" },
    { code: "LA", name: "Louisiana" },
    { code: "ME", name: "Maine" },
    { code: "MD", name: "Maryland" },
    { code: "MA", name: "Massachusetts" },
    { code: "MI", name: "Michigan" },
    { code: "MN", name: "Minnesota" },
    { code: "MS", name: "Mississippi" },
    { code: "MO", name: "Missouri" },
    { code: "MT", name: "Montana" },
    { code: "NE", name: "Nebraska" },
    { code: "NV", name: "Nevada" },
    { code: "NH", name: "New Hampshire" },
    { code: "NJ", name: "New Jersey" },
    { code: "NM", name: "New Mexico" },
    { code: "NY", name: "New York" },
    { code: "NC", name: "North Carolina" },
    { code: "ND", name: "North Dakota" },
    { code: "OH", name: "Ohio" },
    { code: "OK", name: "Oklahoma" },
    { code: "OR", name: "Oregon" },
    { code: "PA", name: "Pennsylvania" },
    { code: "RI", name: "Rhode Island" },
    { code: "SC", name: "South Carolina" },
    { code: "SD", name: "South Dakota" },
    { code: "TN", name: "Tennessee" },
    { code: "TX", name: "Texas" },
    { code: "UT", name: "Utah" },
    { code: "VT", name: "Vermont" },
    { code: "VA", name: "Virginia" },
    { code: "WA", name: "Washington" },
    { code: "WV", name: "West Virginia" },
    { code: "WI", name: "Wisconsin" },
    { code: "WY", name: "Wyoming" },
    { code: "DC", name: "District of Columbia" },
    { code: "PR", name: "Puerto Rico" },
  ],

  // Canada - 10 provinces + 3 territories
  CA: [
    { code: "AB", name: "Alberta" },
    { code: "BC", name: "British Columbia" },
    { code: "MB", name: "Manitoba" },
    { code: "NB", name: "New Brunswick" },
    { code: "NL", name: "Newfoundland and Labrador" },
    { code: "NT", name: "Northwest Territories" },
    { code: "NS", name: "Nova Scotia" },
    { code: "NU", name: "Nunavut" },
    { code: "ON", name: "Ontario" },
    { code: "PE", name: "Prince Edward Island" },
    { code: "QC", name: "Quebec" },
    { code: "SK", name: "Saskatchewan" },
    { code: "YT", name: "Yukon" },
  ],

  // Australia - 6 states + 2 territories
  AU: [
    { code: "NSW", name: "New South Wales" },
    { code: "VIC", name: "Victoria" },
    { code: "QLD", name: "Queensland" },
    { code: "WA", name: "Western Australia" },
    { code: "SA", name: "South Australia" },
    { code: "TAS", name: "Tasmania" },
    { code: "ACT", name: "Australian Capital Territory" },
    { code: "NT", name: "Northern Territory" },
  ],

  // Germany - 16 Federal States
  DE: [
    { code: "BW", name: "Baden-Württemberg" },
    { code: "BY", name: "Bavaria (Bayern)" },
    { code: "BE", name: "Berlin" },
    { code: "BB", name: "Brandenburg" },
    { code: "HB", name: "Bremen" },
    { code: "HH", name: "Hamburg" },
    { code: "HE", name: "Hesse (Hessen)" },
    { code: "MV", name: "Mecklenburg-Vorpommern" },
    { code: "NI", name: "Lower Saxony (Niedersachsen)" },
    { code: "NW", name: "North Rhine-Westphalia (Nordrhein-Westfalen)" },
    { code: "RP", name: "Rhineland-Palatinate (Rheinland-Pfalz)" },
    { code: "SL", name: "Saarland" },
    { code: "SN", name: "Saxony (Sachsen)" },
    { code: "ST", name: "Saxony-Anhalt (Sachsen-Anhalt)" },
    { code: "SH", name: "Schleswig-Holstein" },
    { code: "TH", name: "Thuringia (Thüringen)" },
  ],

  // United Kingdom - 4 constituent countries
  GB: [
    { code: "ENG", name: "England" },
    { code: "SCT", name: "Scotland" },
    { code: "WLS", name: "Wales" },
    { code: "NIR", name: "Northern Ireland" },
  ],

  // France - 18 Administrative Regions
  FR: [
    { code: "IDF", name: "Île-de-France (Paris)" },
    { code: "ARA", name: "Auvergne-Rhône-Alpes (Lyon)" },
    { code: "BFC", name: "Bourgogne-Franche-Comté" },
    { code: "BRE", name: "Brittany (Bretagne)" },
    { code: "CVL", name: "Centre-Val de Loire" },
    { code: "COR", name: "Corsica (Corse)" },
    { code: "GES", name: "Grand Est" },
    { code: "HDF", name: "Hauts-de-France (Lille)" },
    { code: "NOR", name: "Normandy (Normandie)" },
    { code: "NAQ", name: "Nouvelle-Aquitaine (Bordeaux)" },
    { code: "OCC", name: "Occitanie (Toulouse)" },
    { code: "PDL", name: "Pays de la Loire (Nantes)" },
    { code: "PAC", name: "Provence-Alpes-Côte d'Azur (Marseille / Nice)" },
  ],

  // Mexico - Major states
  MX: [
    { code: "CMX", name: "Mexico City (CDMX)" },
    { code: "JAL", name: "Jalisco (Guadalajara)" },
    { code: "NLE", name: "Nuevo León (Monterrey)" },
    { code: "MEX", name: "State of Mexico" },
    { code: "PUE", name: "Puebla" },
    { code: "BCN", name: "Baja California (Tijuana)" },
    { code: "BCS", name: "Baja California Sur" },
    { code: "CHH", name: "Chihuahua" },
    { code: "COA", name: "Coahuila" },
    { code: "GUA", name: "Guanajuato" },
    { code: "MIC", name: "Michoacán" },
    { code: "OAX", name: "Oaxaca" },
    { code: "QRO", name: "Querétaro" },
    { code: "ROO", name: "Quintana Roo (Cancún)" },
    { code: "SIN", name: "Sinaloa" },
    { code: "SON", name: "Sonora" },
    { code: "VER", name: "Veracruz" },
    { code: "YUC", name: "Yucatán (Mérida)" },
  ],

  // Brazil - Major states
  BR: [
    { code: "SP", name: "São Paulo" },
    { code: "RJ", name: "Rio de Janeiro" },
    { code: "MG", name: "Minas Gerais" },
    { code: "BA", name: "Bahia" },
    { code: "PR", name: "Paraná" },
    { code: "RS", name: "Rio Grande do Sul" },
    { code: "PE", name: "Pernambuco" },
    { code: "CE", name: "Ceará" },
    { code: "SC", name: "Santa Catarina" },
    { code: "DF", name: "Distrito Federal (Brasília)" },
  ],

  // United Arab Emirates - 7 Emirates
  AE: [
    { code: "DXB", name: "Dubai" },
    { code: "AUH", name: "Abu Dhabi" },
    { code: "SHJ", name: "Sharjah" },
    { code: "AJM", name: "Ajman" },
    { code: "RAK", name: "Ras Al Khaimah" },
    { code: "FUJ", name: "Fujairah" },
    { code: "UAQ", name: "Umm Al Quwain" },
  ],

  // Saudi Arabia - 13 Provinces
  SA: [
    { code: "RIY", name: "Riyadh Province" },
    { code: "MAK", name: "Makkah Province (Jeddah)" },
    { code: "EAS", name: "Eastern Province (Dammam)" },
    { code: "MED", name: "Madinah Province" },
    { code: "ASI", name: "Asir Province" },
    { code: "TAB", name: "Tabuk Province" },
    { code: "QAS", name: "Al-Qassim Province" },
    { code: "HAI", name: "Hail Province" },
    { code: "JAZ", name: "Jazan Province" },
    { code: "NAJ", name: "Najran Province" },
  ],

  // Spain - 17 Autonomous Communities
  ES: [
    { code: "MD", name: "Madrid" },
    { code: "CT", name: "Catalonia (Barcelona)" },
    { code: "AN", name: "Andalusia (Seville / Málaga)" },
    { code: "VC", name: "Valencian Community" },
    { code: "PV", name: "Basque Country (País Vasco)" },
    { code: "GA", name: "Galicia" },
    { code: "CL", name: "Castile and León" },
    { code: "CM", name: "Castilla-La Mancha" },
    { code: "CN", name: "Canary Islands" },
    { code: "IB", name: "Balearic Islands (Mallorca / Ibiza)" },
  ],

  // Italy - 20 Regions
  IT: [
    { code: "LOM", name: "Lombardy (Milan)" },
    { code: "LAZ", name: "Lazio (Rome)" },
    { code: "CAM", name: "Campania (Naples)" },
    { code: "VEN", name: "Veneto (Venice / Verona)" },
    { code: "SIC", name: "Sicily" },
    { code: "PIE", name: "Piedmont (Turin)" },
    { code: "EMR", name: "Emilia-Romagna (Bologna)" },
    { code: "PUG", name: "Apulia (Puglia)" },
    { code: "TOS", name: "Tuscany (Florence)" },
    { code: "SAR", name: "Sardinia" },
  ],

  // Switzerland - 26 Cantons
  CH: [
    { code: "ZH", name: "Zurich" },
    { code: "BE", name: "Bern" },
    { code: "VD", name: "Vaud (Lausanne)" },
    { code: "GE", name: "Geneva" },
    { code: "BS", name: "Basel-Stadt" },
    { code: "BL", name: "Basel-Landschaft" },
    { code: "TI", name: "Ticino (Lugano)" },
    { code: "SG", name: "St. Gallen" },
    { code: "LU", name: "Lucerne" },
    { code: "VS", name: "Valais" },
    { code: "AG", name: "Aargau" },
    { code: "ZG", name: "Zug" },
  ],
};

export function getSubdivisionsForCountry(countryCode: string): CountrySubdivision[] {
  const normalized = (countryCode || "").trim().toUpperCase();
  return COUNTRY_SUBDIVISIONS[normalized] || [];
}

export function hasPredefinedSubdivisions(countryCode: string): boolean {
  const normalized = (countryCode || "").trim().toUpperCase();
  return normalized in COUNTRY_SUBDIVISIONS && COUNTRY_SUBDIVISIONS[normalized].length > 0;
}
