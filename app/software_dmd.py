"""Python2/3 adapter: keep original 1280x320 renderer; scale only its visible SDL window.
No source game/EP rendering code is replaced or redistributed by this module.
"""
import os


def configure_desktop(pygame, desktop_class, geometry):
    if len(geometry) != 3 or not 1 <= geometry[2] <= 14:
        raise ValueError('Invalid DMD geometry')
    x, y, pixel = geometry
    original_draw_window = desktop_class.draw_window
    original_update = pygame.display.update
    owner = [None]

    def setup_window(self):
        # Native EP's HD image blits assume a 1280x320 backing surface. Shrinking
        # that surface crops HD content; instead keep it and scale the final image.
        os.environ['SDL_VIDEO_WINDOW_POS'] = '%d,%d' % (x, y)
        pygame.init()
        self.visible_window = pygame.display.set_mode((128 * pixel, 32 * pixel), pygame.NOFRAME)
        self.screen = pygame.Surface((1280, 320), depth=24)
        self.dmd_display = self.screen
        self.screen.fill((0, 0, 0))
        pygame.display.set_caption('Cactus Canyon Continued - software DMD')
        owner[0] = self

    def draw_window(self, *unused):
        self.windowless = False
        return original_draw_window(self, 10, -43, -233)

    def update(*unused, **kwargs):
        if owner[0] is not None:
            desktop = owner[0]
            image = pygame.transform.scale(desktop.screen, desktop.visible_window.get_size())
            desktop.visible_window.blit(image, (0, 0))
        return original_update()

    def passive_events(self):
        # Event pumping keeps SDL responsive but never duplicates VPX WPC inputs.
        pygame.event.pump()
        pygame.event.clear()
        return []

    desktop_class.setup_window = setup_window
    desktop_class.draw_window = draw_window
    desktop_class.get_keyboard_events = passive_events
    pygame.display.update = update
