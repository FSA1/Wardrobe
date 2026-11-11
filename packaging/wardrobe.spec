Name:           wardrobe
Version:        1.1.5
Release:        11%{?dist}
Summary:        Wardrobe Plus GNOME theme manager

%global debug_package %{nil}

License:        GPL-3.0-or-later
URL:            https://github.com/FSA1/Wardrobe
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  appstream
BuildRequires:  desktop-file-utils
BuildRequires:  gettext
BuildRequires:  glib2-devel
BuildRequires:  gnome-autoar-devel
BuildRequires:  gtk4-devel
BuildRequires:  libadwaita-devel
BuildRequires:  libportal-gtk4-devel
BuildRequires:  libsoup3-devel
BuildRequires:  meson
BuildRequires:  ninja-build

Requires:       gnome-autoar
Requires:       gtk4
Requires:       libadwaita
Requires:       libportal-gtk4
Requires:       libsoup3
Requires:       python3-gobject

%description
Wardrobe Plus is a GTK 4 and Libadwaita application for browsing, installing,
and managing GNOME Shell, GTK, icon, cursor, and wallpaper themes.
This package builds the FSA1/Wardrobe Plus fork.

%prep
%autosetup -n %{name}-%{version}

%build
%meson
%meson_build

%check
%meson_test

%install
%meson_install
%find_lang wardrobe

%posttrans
gtk4-update-icon-cache -q -t -f %{_datadir}/icons/hicolor

%postun
gtk4-update-icon-cache -q -t -f %{_datadir}/icons/hicolor

%files -f wardrobe.lang
%license COPYING
%doc README.md
%{_bindir}/wardrobe
%{_datadir}/wardrobe/
%{_datadir}/applications/io.github.swordpuffin.wardrobe.desktop
%{_datadir}/dbus-1/services/io.github.swordpuffin.wardrobe.service
%{_datadir}/glib-2.0/schemas/io.github.swordpuffin.wardrobe.gschema.xml
%{_datadir}/icons/hicolor/scalable/actions/
%{_datadir}/icons/hicolor/scalable/apps/io.github.swordpuffin.wardrobe.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github.swordpuffin.wardrobe-symbolic.svg
%{_datadir}/metainfo/io.github.swordpuffin.wardrobe.metainfo.xml
